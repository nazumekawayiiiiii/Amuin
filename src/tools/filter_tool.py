"""Filter tool — apply search filter conditions.

Handles salary, experience, degree, industry, scale filters via ka attributes.
City filter has special dialog handling.
"""

import logging

from patchright.sync_api import Page

from .base_tool import (
    BaseTool, SelectorNotFoundError,
    evaluate_main_world, wait_for_main_world, load_tool_config,
)


log = logging.getLogger("claw")


class FilterTool(BaseTool):
    """Apply filter conditions on the BOSS直聘 job search page."""

    def __init__(self, page: Page, tool_config: dict, common_config: dict,
                 filter_codes: dict):
        super().__init__(page, tool_config, common_config)
        self._filter_codes = filter_codes

    def apply_filter(self, dimension: str, value: str) -> bool:
        """Apply a single filter condition.

        Args:
            dimension: One of "salary", "experience", "degree",
                       "industry", "scale", "city".
            value: Human-readable value (e.g., "10-20K", "3-5年", "本科").

        Returns:
            True if filter was applied successfully.
        """
        if dimension == "city":
            return self._apply_city_filter(value)

        ka_prefix = self._config["ka_prefixes"].get(dimension)
        if not ka_prefix:
            log.warning(f"[WORK] 未知的筛选维度: {dimension}")
            return False

        # Industry ka uses sequential index (0,1,2...), not filter code.
        # Select by exact text matching — user must enter the exact label.
        if dimension == "industry":
            return self._select_option_by_text(dimension, ka_prefix, value)

        code = self._resolve_filter_value(dimension, value)
        if code is None:
            log.warning(f"[WORK] 未知的筛选值: {dimension}={value}")
            return False

        return self._select_option_by_ka(dimension, ka_prefix, code)

    def apply_filters(self, filters: dict[str, str]) -> dict[str, bool]:
        """Apply multiple filter conditions. City is always applied first.

        Args:
            filters: Dict mapping dimension to value,
                     e.g., {"salary": "10-20K", "experience": "3-5年"}.

        Returns:
            Dict mapping dimension to success status.
        """
        results = {}

        # Clear previous filters to avoid stale state between combinations
        self.clear_all()

        # City MUST go first (causes page reload on BOSS直聘)
        city_value = filters.get("city")
        if city_value and city_value != "不限":
            results["city"] = self.apply_filter("city", city_value)
            if results["city"]:
                self._wait_after_city_change()

        # Remaining filters
        for dimension, value in filters.items():
            if dimension == "city":
                continue
            if not value or value == "不限":
                continue
            results[dimension] = self.apply_filter(dimension, value)
            self._random_delay(0.5, 1.5)
        return results

    def _resolve_filter_value(self, dimension: str, value: str) -> int | None:
        """Resolve user-entered value to a filter code with fuzzy matching."""
        codes = self._filter_codes.get(dimension, {})

        # Exact match (fast path, no logging)
        if value in codes:
            return codes[value]

        # Fuzzy match with logging
        code = _fuzzy_resolve(value, codes)
        if code is not None:
            # Find the matched key for logging
            for key, c in codes.items():
                if c == code:
                    log.info(f"[WORK] 筛选值匹配: '{value}' → '{key}'")
                    break
        return code

    def _resolve_to_text(self, dimension: str, value: str) -> str | None:
        """Resolve user-entered value to the canonical text label.

        Like _resolve_filter_value but returns the matched key name
        instead of the code. Used for industry where ka uses index.
        """
        codes = self._filter_codes.get(dimension, {})
        if value in codes:
            return value
        # Fuzzy match — return the key, not the code
        value_lower = value.lower()
        for key in codes:
            if key.lower() == value_lower:
                log.info(f"[WORK] 筛选值匹配: '{value}' → '{key}'")
                return key
        candidates = [k for k in codes if value in k or value_lower in k.lower()]
        if len(candidates) == 1:
            log.info(f"[WORK] 筛选值匹配: '{value}' → '{candidates[0]}'")
            return candidates[0]
        if len(candidates) > 1:
            best = min(candidates, key=len)
            log.info(f"[WORK] 筛选值匹配: '{value}' → '{best}'")
            return best
        return None

    def _wait_after_city_change(self) -> None:
        """Wait for page to stabilize after city change triggers reload.

        City selection on BOSS直聘 causes an async page refresh.
        We must wait for the NEW page, not the old one. Strategy:
          1. Wait for URL to change (contains city parameter)
          2. Wait for fresh Vue $children on the new DOM
        """
        import time

        # 1. Wait for URL to reflect city change (navigation hasn't started yet)
        old_url = self._page.url
        deadline = time.time() + 10
        while time.time() < deadline:
            current = self._page.url
            if current != old_url:
                break
            time.sleep(0.3)

        # 2. Wait for new page to load
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

        self._random_delay(1, 2)

        # 3. Wait for filter bar Vue $children on the NEW page
        mount = self._config["filter_dropdown_vue"]["mount_selector"]
        vue_js = (
            f"!!(document.querySelector('{mount}')?.__vue__"
            f"?.$children?.length)"
        )
        result = wait_for_main_world(self._page, vue_js, timeout=10000)
        if not result:
            log.warning("[WORK] 城市切换后筛选栏未就绪")

        self._random_delay(0.5, 1)

    def clear_all(self) -> None:
        """Clear all filter conditions (if any are active)."""
        sel = self._config["elements"]["clear_all"]["selector"]
        try:
            btn = self._page.query_selector(sel)
            if btn:
                btn.evaluate("el => el.click()")
                self._random_delay(1, 2)
                log.info("[WORK] 已清除所有筛选条件")
        except Exception:
            pass

    def _select_option_by_ka(
        self, dimension: str, ka_prefix: str, code: int
    ) -> bool:
        """Hover a filter dropdown to open it, then click an option by ka attribute.

        BOSS直聘's filter dropdowns are hover-triggered (CSS :hover / mouseenter),
        not click-triggered. This matches geekgeekrun's approach.
        """
        mount = self._config["filter_dropdown_vue"]["mount_selector"]

        placeholder_map = {
            "salary": "薪资待遇",
            "experience": "工作经验",
            "degree": "学历要求",
            "industry": "公司行业",
            "scale": "公司规模",
        }
        placeholder = placeholder_map.get(dimension, "")
        if not placeholder:
            return False

        # Get bounding box of the dropdown entry via Vue $children
        bbox_js = f"""(() => {{
            const bar = document.querySelector('{mount}');
            if (!bar || !bar.__vue__) return null;
            const entry = bar.__vue__.$children.find(
                it => it.placeholder === '{placeholder}'
            );
            if (entry && entry.$el) {{
                entry.$el.scrollIntoView({{block: 'center'}});
                const rect = entry.$el.getBoundingClientRect();
                return {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}};
            }}
            return null;
        }})()"""

        bbox = evaluate_main_world(self._page, bbox_js)
        if not bbox:
            log.warning(f"[WORK] 筛选入口未找到: {dimension}")
            return False

        # Click the option with matching ka attribute
        option_selector = (
            f'.page-jobs-main .filter-condition-inner [ka="{ka_prefix}{code}"]'
        )

        # Try up to 2 times — filter bar may re-render after previous selection
        for attempt in range(2):
            # Re-fetch bounding box on retry (DOM may have shifted)
            if attempt > 0:
                self._hover_away()
                self._random_delay(0.3, 0.5)
                bbox = evaluate_main_world(self._page, bbox_js)
                if not bbox:
                    break

            # Hover to open dropdown (not click!)
            self._page.mouse.move(
                bbox["x"] + bbox["w"] / 2,
                bbox["y"] + bbox["h"] / 2,
            )
            self._random_delay(0.3, 0.8)

            try:
                option = self._page.wait_for_selector(
                    option_selector, timeout=3000
                )
                if option:
                    option.evaluate("el => el.click()")
                    self._random_delay(0.3, 0.6)
                    self._hover_away()
                    log.info(f"[WORK] 筛选: {dimension}={code}")
                    return True
            except Exception:
                pass

        # Close dropdown on failure
        self._hover_away()
        log.warning(f"[WORK] 筛选选项未找到: {option_selector}")
        return False

    def _hover_away(self) -> None:
        """Move mouse to header logo to close any open dropdown."""
        try:
            logo = self._page.query_selector('[ka="header-home-logo"]')
            if logo:
                bbox = logo.bounding_box()
                if bbox:
                    self._page.mouse.move(
                        bbox["x"] + bbox["width"] / 2,
                        bbox["y"] + bbox["height"] / 2,
                    )
                    return
        except Exception:
            pass
        # Fallback: move to top-left area
        self._page.mouse.move(100, 10)

    def _select_option_by_text(
        self, dimension: str, ka_prefix: str, text: str
    ) -> bool:
        """Hover dropdown open, then select an option by text content.

        Used for industry filter where ka uses sequential index (0,1,2...)
        instead of the filter code, making ka-based lookup unreliable.
        """
        mount = self._config["filter_dropdown_vue"]["mount_selector"]
        placeholder = "公司行业"

        # Get bounding box via Vue $children
        bbox_js = f"""(() => {{
            const bar = document.querySelector('{mount}');
            if (!bar || !bar.__vue__) return null;
            const entry = bar.__vue__.$children.find(
                it => it.placeholder === '{placeholder}'
            );
            if (entry && entry.$el) {{
                entry.$el.scrollIntoView({{block: 'center'}});
                const rect = entry.$el.getBoundingClientRect();
                return {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}};
            }}
            return null;
        }})()"""

        bbox = evaluate_main_world(self._page, bbox_js)
        if not bbox:
            log.warning(f"[WORK] 筛选入口未找到: {dimension}")
            return False

        # Hover to open dropdown
        self._page.mouse.move(
            bbox["x"] + bbox["w"] / 2,
            bbox["y"] + bbox["h"] / 2,
        )
        self._random_delay(0.3, 0.8)

        # Find all industry options and match by text
        options = self._page.query_selector_all(
            f'.page-jobs-main .filter-condition-inner [ka^="{ka_prefix}"]'
        )
        for opt in options:
            opt_text = (opt.inner_text() or "").strip()
            if opt_text == text:
                opt.evaluate("el => el.click()")
                self._random_delay(0.3, 0.6)
                self._hover_away()
                log.info(f"[WORK] 筛选: {dimension}={text}")
                return True

        # Close dropdown on failure
        self._hover_away()
        log.warning(f"[WORK] 行业选项未找到: {text}")
        return False

    def _apply_city_filter(self, city_name: str) -> bool:
        """Apply city filter via the special city selection dialog."""
        try:
            self._click("city_button", timeout=3000)
        except SelectorNotFoundError:
            log.warning("[WORK] 城市筛选按钮未找到")
            return False

        # Wait for city dialog to be visible
        try:
            self._page.wait_for_function(
                """() => {
                    const d = document.querySelector('.city-select-dialog');
                    return d && window.getComputedStyle(d).display !== 'none';
                }""",
                timeout=5000,
            )
        except Exception:
            log.warning("[WORK] 城市选择对话框未出现")
            return False

        self._random_delay(0.3, 0.6)

        # Try hot city list first
        hot_items = self._page.query_selector_all(
            self._config["elements"]["city_hot_list"]["selector"]
        )
        for item in hot_items:
            text = (item.inner_text() or "").strip()
            if text == city_name:
                item.click()
                self._random_delay(0.5, 1)
                log.info(f"[WORK] 筛选城市: {city_name}（热门）")
                return True

        # Try alphabetical navigation
        if city_name:
            first_char = city_name[0].upper()
            # Click the character in the alphabet list
            char_xpath = (
                f'//*[contains(@class, "city-select-dialog")]'
                f'//*[contains(@class, "city-select-wrapper")]'
                f'//ul[contains(@class, "city-char-list")]'
                f'//li[contains(text(), "{first_char}")]'
            )
            try:
                char_el = self._page.wait_for_selector(
                    f"xpath={char_xpath}", timeout=3000
                )
                if char_el:
                    char_el.click()
                    self._random_delay(0.3, 0.5)

                    # Find city in the filtered list
                    result_items = self._page.query_selector_all(
                        self._config["elements"]["city_result_list"]["selector"]
                    )
                    for item in result_items:
                        text = (item.inner_text() or "").strip()
                        if text == city_name:
                            item.click()
                            self._random_delay(0.5, 1)
                            log.info(f"[WORK] 筛选城市: {city_name}")
                            return True
            except Exception:
                pass

        log.warning(f"[WORK] 城市未找到: {city_name}")
        # Close dialog by clicking away
        try:
            self._click("header_logo", timeout=2000)
        except SelectorNotFoundError:
            pass
        return False

    # ── Static validation (no Page needed) ──

    @staticmethod
    def validate_filter_values(
        traversal: dict, filter_codes: dict
    ) -> list[str]:
        """Validate traversal filter values against known filter_codes.

        Returns list of warning strings for values that can't be resolved.
        Safe to call from UI thread (no browser dependency).
        """
        warnings = []
        dim_map = {
            "salary": "salary",
            "experience": "experience",
            "degree": "degree",
            "industry": "industry",
            "scale": "scale",
        }
        for trav_key, dim in dim_map.items():
            values = traversal.get(trav_key, [])
            codes = filter_codes.get(dim, {})
            for val in values:
                if not val or val == "不限":
                    continue
                if _fuzzy_resolve(val, codes) is not None:
                    continue
                valid = ", ".join(k for k in codes if k != "不限")
                warnings.append(f"{trav_key}: '{val}' 无法匹配。可选: {valid}")
        return warnings


def _fuzzy_resolve(value: str, codes: dict) -> int | None:
    """Shared fuzzy resolution logic (module-level for reuse)."""
    if value in codes:
        return codes[value]
    value_lower = value.lower()
    for key, code in codes.items():
        if key.lower() == value_lower:
            return code
    candidates = [
        (k, c) for k, c in codes.items()
        if value in k or value_lower in k.lower()
    ]
    if len(candidates) == 1:
        return candidates[0][1]
    if len(candidates) > 1:
        return min(candidates, key=lambda x: len(x[0]))[1]
    return None
