"""Tool validator — verify tool_config.json selectors against live pages.

Three-level validation system:
  Level 1: Selector matches element, correct count and tag
  Level 2: Element attribute checks
  Level 3: Operation produces expected effect (optional, has side effects)

Runs during initialization. Critical failures block startup.
"""

import time
import logging
from dataclasses import dataclass, field

from patchright.sync_api import Page

from ..tools.base_tool import load_tool_config, evaluate_main_world


log = logging.getLogger("claw")


@dataclass
class CheckResult:
    """Result of a single validation check."""

    name: str
    level: int  # 1, 2, or 3
    passed: bool
    message: str
    critical: bool = False


@dataclass
class ValidationReport:
    """Aggregated validation results."""

    results: list[CheckResult] = field(default_factory=list)

    def add(
        self, name: str, level: int, passed: bool, message: str,
        critical: bool = False,
    ) -> None:
        self.results.append(CheckResult(name, level, passed, message, critical))

    @property
    def passed(self) -> bool:
        """True if no critical checks failed."""
        return not any(r.critical and not r.passed for r in self.results)

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and not r.critical]

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.critical]

    def log_summary(self) -> None:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.passed)
        log.info(f"[INIT] 工具验证: {ok}/{total} 通过")

        for r in self.results:
            if r.passed:
                log.debug(f"  ✓ L{r.level} {r.name}")
            elif r.critical:
                log.error(f"  ✗ L{r.level} [关键] {r.name}: {r.message}")
            else:
                log.warning(f"  ⚠ L{r.level} {r.name}: {r.message}")


class ToolValidator:
    """Validates tool selectors and Vue data channels on the live page.

    Usage:
        validator = ToolValidator(page)
        report = validator.validate()
        if not report.passed:
            # Critical selectors broken, cannot proceed
    """

    def __init__(self, page: Page):
        self._page = page
        self._config = load_tool_config()

    def validate(self, run_level3: bool = False) -> ValidationReport:
        """Run validation checks.

        Args:
            run_level3: If True, also run Level 3 interaction checks
                        (clicks a job card — minimal side effect).

        Returns:
            ValidationReport with all check results.
        """
        report = ValidationReport()

        # Navigate to jobs page
        url = self._config["common"]["urls"]["jobs_page"]
        log.info(f"[INIT] 工具验证: 导航到 {url}")
        try:
            self._page.goto(url, wait_until="domcontentloaded")
        except Exception as e:
            # ERR_ABORTED is common — BOSS直聘 does client-side redirects
            log.warning(f"[INIT] 导航异常 (可能是重定向): {e}")
        time.sleep(3)  # Wait for Vue to mount

        # Verify we landed on a valid page
        current_url = self._page.url or ""
        if "zhipin.com" not in current_url:
            report.add(
                "navigation", 1, False,
                f"未到达目标页面 (当前: {current_url})", critical=True,
            )
            report.log_summary()
            return report

        # Close security popup if present
        self._close_security_popup()

        # Phase A: on-load checks (Level 1 + 2)
        self._validate_on_load(report)

        # If Vue mount failed, run a broad diagnostic
        if not report.passed:
            self._run_vue_diagnostic()

        # Phase B: interaction checks (Level 3)
        if run_level3 and report.passed:
            self._validate_with_interaction(report)

        report.log_summary()
        return report

    # ── Phase A: On-Load Checks ──

    def _validate_on_load(self, report: ValidationReport) -> None:
        """Level 1+2 checks for elements visible after page load."""

        # -- Search Tool --
        self._check_element(
            report, "search_tool", "search_box",
            expected_tag="input", critical=True,
        )
        self._check_element(
            report, "search_tool", "recommend_tab",
            expected_tag="a", critical=False,
        )

        # -- Filter Tool --
        self._check_element(
            report, "filter_tool", "filter_bar",
            critical=True,
        )
        self._check_element(
            report, "filter_tool", "header_logo",
            expected_tag="a", critical=False,
        )
        # Check filter dropdown Vue integration
        self._check_filter_vue(report)

        # -- Job List (cards should exist from recommendations) --
        self._check_element(
            report, "job_detail_tool", "job_cards",
            min_count=1, critical=True,
        )

        # -- Vue Data Channel --
        self._check_vue_mount(report, ".page-jobs-main", critical=True)
        self._check_data_extract(
            report, "search_tool", "job_list",
            expected_type="array", critical=True,
        )
        self._check_data_extract(
            report, "search_tool", "has_more",
            expected_type="boolean", critical=False,
        )

    # ── Phase B: Interaction Checks ──

    def _validate_with_interaction(self, report: ValidationReport) -> None:
        """Level 3: click a job card and verify detail panel."""
        cards_sel = self._get_selector("job_detail_tool", "job_cards")
        cards = self._page.query_selector_all(cards_sel)
        if not cards:
            report.add(
                "detail_interaction", 3, False,
                "无法验证详情面板（职位列表为空）", critical=True,
            )
            return

        # Click the SECOND card (first may already be pre-loaded,
        # which wouldn't trigger a detail API call)
        target_idx = min(1, len(cards) - 1)
        api_pattern = self._config["job_detail_tool"]["api_endpoints"][
            "detail_loaded"
        ]["url_pattern"]

        try:
            with self._page.expect_response(
                lambda r: api_pattern in r.url, timeout=10000,
            ):
                cards[target_idx].click()
            time.sleep(1)
            report.add("detail_api_response", 3, True, "详情API正常响应")
        except Exception as e:
            # API might not fire (cached, pre-loaded, etc.)
            # Not critical — the actual search loop handles this differently
            report.add(
                "detail_api_response", 3, False,
                f"详情API无响应 (可能已缓存): {e}", critical=False,
            )
            time.sleep(2)  # Wait for detail to load via other means

        # Check detail panel elements
        self._check_element(
            report, "job_detail_tool", "detail_box",
            critical=True,
        )
        self._check_element(
            report, "chat_tool", "chat_button",
            critical=True,
        )
        self._check_element(
            report, "mark_tool", "not_suitable_button",
            critical=False,
        )

        # Check detail Vue data
        self._check_data_extract(
            report, "job_detail_tool", "job_detail",
            expected_type="object", critical=True,
        )

        # Validate detail data has expected structure
        self._check_detail_structure(report)

    # ── Check Helpers ──

    def _check_element(
        self,
        report: ValidationReport,
        tool_name: str,
        element_name: str,
        expected_tag: str | None = None,
        min_count: int = 1,
        critical: bool = False,
    ) -> bool:
        """Level 1+2: find element, check count and tag."""
        selector = self._get_selector(tool_name, element_name)
        check_name = f"{tool_name}.{element_name}"

        # Level 1: Does selector match anything?
        elements = self._page.query_selector_all(selector)
        count = len(elements)

        if count < min_count:
            report.add(
                check_name, 1, False,
                f"选择器未匹配 (找到 {count}, 期望 >= {min_count}): {selector}",
                critical=critical,
            )
            return False

        report.add(check_name, 1, True, f"匹配 {count} 个元素")

        # Level 2: Tag check
        if expected_tag and elements:
            actual_tag = elements[0].evaluate("el => el.tagName.toLowerCase()")
            if actual_tag != expected_tag.lower():
                report.add(
                    f"{check_name}.tag", 2, False,
                    f"标签不匹配 (实际: {actual_tag}, 期望: {expected_tag})",
                    critical=critical,
                )
                return False
            report.add(f"{check_name}.tag", 2, True, f"标签: {actual_tag}")

        return True

    def _check_vue_mount(
        self, report: ValidationReport, mount_selector: str,
        critical: bool = False,
    ) -> bool:
        """Level 1: check Vue instance exists on mount element."""
        check_name = f"vue_mount({mount_selector})"

        # Check mount element exists
        el = self._page.query_selector(mount_selector)
        if not el:
            report.add(
                check_name, 1, False,
                f"挂载元素不存在: {mount_selector}", critical=critical,
            )
            return False

        # Check __vue__ via main-world bridge
        has_vue = evaluate_main_world(
            self._page,
            f'!!document.querySelector("{mount_selector}")?.__vue__',
        )
        if has_vue:
            report.add(check_name, 1, True, "Vue 实例存在 (主世界桥接)")
            return True

        report.add(
            check_name, 1, False,
            f"__vue__ 不存在: {mount_selector}", critical=critical,
        )
        return False

    def _check_data_extract(
        self,
        report: ValidationReport,
        tool_name: str,
        extract_name: str,
        expected_type: str | None = None,
        critical: bool = False,
    ) -> bool:
        """Level 1+2: check Vue data extraction returns expected type."""
        cfg = self._config[tool_name]["data_extracts"][extract_name]
        mount = cfg["mount_selector"]
        path = cfg["path"]
        check_name = f"{tool_name}.data.{extract_name}"

        # Level 1: Can we read the value?
        # Use main-world bridge for Vue data access
        js = f'document.querySelector("{mount}")?.{path}'
        value = evaluate_main_world(self._page, js)

        if value is None:
            report.add(
                check_name, 1, False,
                f"数据为 null: {mount} → {path}", critical=critical,
            )
            return False

        report.add(check_name, 1, True, f"数据可读")

        # Level 2: Type check
        if expected_type:
            type_js = (
                f'(function(){{'
                f'var v=document.querySelector("{mount}")?.{path};'
                f'if(v===null||v===undefined)return "null";'
                f'if(Array.isArray(v))return "array";'
                f'return typeof v;'
                f'}})()'
            )
            actual_type = evaluate_main_world(self._page, type_js)

            if actual_type != expected_type:
                report.add(
                    f"{check_name}.type", 2, False,
                    f"类型不匹配 (实际: {actual_type}, 期望: {expected_type})",
                    critical=critical,
                )
                return False
            report.add(f"{check_name}.type", 2, True, f"类型: {actual_type}")

        return True

    def _check_filter_vue(self, report: ValidationReport) -> None:
        """Level 2: check filter bar Vue $children accessibility."""
        cfg = self._config["filter_tool"]["filter_dropdown_vue"]
        mount = cfg["mount_selector"]

        has_children = evaluate_main_world(
            self._page,
            f"!!(document.querySelector('{mount}')?.__vue__?.$children?.length)",
        )
        report.add(
            "filter_tool.vue_children", 2,
            bool(has_children),
            "Vue $children 可访问" if has_children else "Vue $children 不可访问",
            critical=False,
        )

    def _check_detail_structure(self, report: ValidationReport) -> None:
        """Level 2: verify job detail data has expected sub-keys."""
        mount = self._config["job_detail_tool"]["data_extracts"][
            "job_detail"
        ]["mount_selector"]
        path = self._config["job_detail_tool"]["data_extracts"][
            "job_detail"
        ]["path"]

        has_structure = evaluate_main_world(
            self._page,
            f'(function(){{'
            f'var d=document.querySelector("{mount}")?.{path};'
            f'if(!d)return false;'
            f'return !!(d.jobInfo&&d.bossInfo&&d.brandComInfo);'
            f'}})()',
        )
        report.add(
            "job_detail.structure", 2,
            bool(has_structure),
            "jobInfo/bossInfo/brandComInfo 结构完整"
            if has_structure
            else "详情数据结构缺失",
            critical=True,
        )

    # ── Utilities ──

    def _get_selector(self, tool_name: str, element_name: str) -> str:
        return self._config[tool_name]["elements"][element_name]["selector"]

    def _close_security_popup(self) -> None:
        """Try to close the security question popup."""
        popup = self._config["common"]["popups"].get("security_question", {})
        sel = popup.get("selector", "")
        close_sel = popup.get("close_selector", "")
        if not sel:
            return
        try:
            el = self._page.wait_for_selector(
                sel, timeout=popup.get("timeout_ms", 3000)
            )
            if el and close_sel:
                time.sleep(0.5)
                close_btn = self._page.query_selector(close_sel)
                if close_btn:
                    close_btn.click()
                    log.info("[INIT] 已关闭安全提问弹窗")
        except Exception:
            pass  # No popup, continue

    def _run_vue_diagnostic(self) -> None:
        """Broad diagnostic to determine why __vue__ is inaccessible.

        Tests two hypotheses:
          A) Patchright's isolated execution context hides page-set JS properties
          B) BOSS直聘 genuinely doesn't expose __vue__ (Vue 3 production mode)

        Uses a <script> tag trick: code inside <script> always runs in the
        page's main world, and writes results to a data attribute (readable
        from any execution context).
        """
        log.info("[INIT] === Vue 诊断开始 ===")

        # ── Test 1: Isolation detection ──
        # Inject a <script> that checks __vue__ in the MAIN world,
        # writes result to a DOM attribute (readable from any context).
        result = self._page.evaluate("""(() => {
            // Create script that runs in the page's main JavaScript world
            const script = document.createElement('script');
            script.textContent = `
                (function() {
                    const el = document.querySelector('.page-jobs-main');
                    const info = {
                        hasVue2: !!(el && el.__vue__),
                        hasVue3App: !!(el && el.__vue_app__),
                        elExists: !!el,
                        dunders: el
                            ? Object.keys(el).filter(k => k.startsWith('__')).slice(0, 10)
                            : [],
                        ownProps: el
                            ? Object.getOwnPropertyNames(el).filter(k => k.startsWith('__')).slice(0, 10)
                            : [],
                        windowVue: typeof window.Vue,
                        vueVersion: (window.Vue && window.Vue.version) || 'N/A',
                    };
                    document.body.setAttribute(
                        'data-claw-vue-diag',
                        JSON.stringify(info)
                    );
                })();
            `;
            document.head.appendChild(script);
            document.head.removeChild(script);

            // Read the result from DOM attribute (works in any context)
            const raw = document.body.getAttribute('data-claw-vue-diag');
            const mainWorldResult = raw ? JSON.parse(raw) : null;

            // Also check from THIS context (evaluate's context)
            const el = document.querySelector('.page-jobs-main');
            const evalContext = {
                hasVue2: !!(el && el.__vue__),
                hasVue3App: !!(el && el.__vue_app__),
                elExists: !!el,
                dunders: el
                    ? Object.keys(el).filter(k => k.startsWith('__')).slice(0, 10)
                    : [],
            };

            // Clean up
            document.body.removeAttribute('data-claw-vue-diag');

            return { mainWorld: mainWorldResult, evalContext: evalContext };
        })()""")

        if result:
            mw = result.get("mainWorld", {})
            ec = result.get("evalContext", {})

            log.info(f"[INIT] 诊断 — 主世界: Vue2={mw.get('hasVue2')}, "
                     f"Vue3={mw.get('hasVue3App')}, "
                     f"window.Vue={mw.get('windowVue')}, "
                     f"version={mw.get('vueVersion')}")
            log.info(f"[INIT] 诊断 — 主世界 __属性: "
                     f"keys={mw.get('dunders')}, "
                     f"ownProps={mw.get('ownProps')}")
            log.info(f"[INIT] 诊断 — evaluate上下文: Vue2={ec.get('hasVue2')}, "
                     f"Vue3={ec.get('hasVue3App')}, "
                     f"__属性={ec.get('dunders')}")

            # Determine diagnosis
            mw_has_vue = mw.get("hasVue2") or mw.get("hasVue3App")
            ec_has_vue = ec.get("hasVue2") or ec.get("hasVue3App")

            if mw_has_vue and not ec_has_vue:
                log.warning(
                    "[INIT] 诊断结论: Patchright 隔离执行上下文 — "
                    "__vue__ 在主世界存在但 evaluate 看不到。"
                    "需要通过 <script> 标签桥接数据。"
                )
            elif not mw_has_vue and not ec_has_vue:
                log.warning(
                    "[INIT] 诊断结论: __vue__ 在主世界也不存在 — "
                    "BOSS直聘可能已升级 Vue 3 生产模式或更换框架。"
                    "需要改用 API 响应拦截获取数据。"
                )
            elif mw_has_vue and ec_has_vue:
                log.info(
                    "[INIT] 诊断结论: __vue__ 可正常访问 — "
                    "可能是时序问题，需要增加等待时间。"
                )
        else:
            log.warning("[INIT] 诊断: 无法执行诊断脚本")

        log.info("[INIT] === Vue 诊断结束 ===")
