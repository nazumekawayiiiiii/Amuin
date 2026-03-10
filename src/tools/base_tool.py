"""Base class for all Patchright-based tools.

Provides config-driven element finding, Vue data extraction,
API response interception, error detection, and anti-detection delays.

NOTE: Patchright evaluates JS in an isolated execution context that
cannot see page-set properties (like Vue's __vue__). All Vue data
access goes through evaluate_main_world(), which bridges via a
<script> tag trick.
"""

import json
import time
import random
import logging
from typing import Any

from patchright.sync_api import Page, Response, ElementHandle

from ..utils.paths import CONFIG_DIR as _CONFIG_DIR

log = logging.getLogger("claw")


class ToolError(Exception):
    """Base exception for tool errors."""


class AccessDeniedError(ToolError):
    """403 or risk control page detected."""


class SecurityCheckError(ToolError):
    """Slider captcha or security check required."""


class DailyLimitError(ToolError):
    """Daily chat limit reached."""


class SelectorNotFoundError(ToolError):
    """Element selector not found on page."""


def load_tool_config() -> dict:
    """Load tool_config.json from config directory."""
    path = _CONFIG_DIR / "tool_config.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_main_world(page: Page, js_expression: str) -> Any:
    """Evaluate a JavaScript expression in the page's main world.

    Patchright's page.evaluate() runs in an isolated V8 context that
    cannot see custom properties set by the page's JavaScript (e.g.,
    Vue's ``__vue__`` on DOM elements).

    This function works around the limitation by:
      1. Creating a ``<script>`` element (always runs in the main world)
      2. The script evaluates the expression and writes the result
         to a DOM data attribute (shared between all contexts)
      3. Reading the data attribute from the isolated context

    Args:
        page: The Patchright Page instance.
        js_expression: JavaScript expression to evaluate.
            Example: ``document.querySelector(".x").__vue__.jobList``

    Returns:
        The deserialized result, or None on error.
    """
    # Inner script: runs in the main world where __vue__ is visible
    inner_script = (
        'try{'
        f'var __r=({js_expression});'
        'document.body.setAttribute("data-claw-bridge",'
        'JSON.stringify(__r===undefined?null:__r));'
        '}catch(e){'
        'document.body.setAttribute("data-claw-bridge",'
        'JSON.stringify({"__bridge_error__":e.message}));'
        '}'
    )

    # json.dumps handles all JS string escaping
    escaped = json.dumps(inner_script)

    outer_js = (
        '(()=>{'
        'const s=document.createElement("script");'
        f's.textContent={escaped};'
        'document.head.appendChild(s);'
        'document.head.removeChild(s);'
        'const r=document.body.getAttribute("data-claw-bridge");'
        'document.body.removeAttribute("data-claw-bridge");'
        'try{return JSON.parse(r)}catch(e){return null}'
        '})()'
    )

    result = page.evaluate(outer_js)

    if isinstance(result, dict) and "__bridge_error__" in result:
        log.warning(
            f"[TOOL] 主世界执行错误: {result['__bridge_error__']}"
        )
        return None

    return result


def wait_for_main_world(
    page: Page, js_expression: str,
    timeout: int = 10000, poll_ms: int = 500,
) -> Any:
    """Poll a JavaScript expression in the main world until truthy.

    Replacement for ``page.wait_for_function()`` when the expression
    needs access to page-set properties like ``__vue__``.

    Args:
        page: The Patchright Page instance.
        js_expression: JS expression that should eventually return truthy.
        timeout: Max wait time in milliseconds.
        poll_ms: Polling interval in milliseconds.

    Returns:
        The first truthy result, or None on timeout.
    """
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        result = evaluate_main_world(page, js_expression)
        if result:
            return result
        time.sleep(poll_ms / 1000)
    return None


class BaseTool:
    """Base class providing common operations for all tools.

    Subclasses get their own config section from tool_config.json
    and share the same Playwright Page instance.
    """

    def __init__(self, page: Page, tool_config: dict, common_config: dict):
        self._page = page
        self._config = tool_config
        self._common = common_config

    # ── Element operations ──

    def _find_element(
        self, element_name: str, timeout: int = 5000
    ) -> ElementHandle:
        """Find element using primary selector, then fallbacks.

        Raises SelectorNotFoundError if all selectors fail.
        """
        elem_cfg = self._config["elements"][element_name]
        selector = elem_cfg["selector"]

        try:
            el = self._page.wait_for_selector(selector, timeout=timeout)
            if el:
                return el
        except Exception:
            pass

        for fallback in elem_cfg.get("fallback_selectors", []):
            try:
                el = self._page.wait_for_selector(fallback, timeout=3000)
                if el:
                    log.warning(
                        f"[MAINT] {element_name}: 主选择器失效，使用 fallback"
                    )
                    return el
            except Exception:
                continue

        raise SelectorNotFoundError(
            f"元素 '{element_name}' 未找到: {selector}"
        )

    def _click(self, element_name: str, timeout: int = 5000) -> None:
        """Find and click an element."""
        el = self._find_element(element_name, timeout)
        el.click()

    def _fill(self, element_name: str, text: str, timeout: int = 5000) -> None:
        """Find an input element, clear it, and type text."""
        el = self._find_element(element_name, timeout)
        el.click()
        # Clear existing text
        current = el.evaluate("el => el.value")
        if current:
            el.select_text()
            self._page.keyboard.press("Backspace")
        el.type(text, delay=80 + random.randint(0, 40))

    # ── Vue data extraction ──

    def _extract_vue_data(self, extract_name: str) -> Any:
        """Extract data from a Vue component instance.

        Uses data_extracts config section. Bridges through the main
        world to access __vue__ properties.
        """
        cfg = self._config["data_extracts"][extract_name]
        mount = cfg["mount_selector"]
        path = cfg["path"]
        js = f'document.querySelector("{mount}")?.{path}'
        return evaluate_main_world(self._page, js)

    # ── API response interception ──

    def _wait_for_api(
        self, endpoint_name: str, timeout: int = 30000
    ) -> Response:
        """Wait for a specific API response matching the configured URL pattern."""
        cfg = self._config["api_endpoints"][endpoint_name]
        pattern = cfg["url_pattern"]
        return self._page.wait_for_response(
            lambda resp: pattern in resp.url,
            timeout=timeout,
        )

    # ── Error detection ──

    def check_error_page(self) -> None:
        """Check if browser is on a 403/error/security-check page.

        Raises appropriate exception if detected.
        """
        url = self._page.url
        for error_url in self._common.get("error_pages", []):
            if url.startswith(error_url):
                if "403" in url:
                    raise AccessDeniedError(f"被风控拦截: {url}")
                if "security-check" in url:
                    raise SecurityCheckError(f"需要安全验证: {url}")
                raise ToolError(f"错误页面: {url}")

        captcha = self._common.get("captcha_page", "")
        if captcha and url.startswith(captcha):
            raise SecurityCheckError(f"滑块验证: {url}")

    # ── Popup handling ──

    def close_popup(self, popup_name: str) -> bool:
        """Try to close a known popup. Returns True if found and closed."""
        popup_cfg = self._common.get("popups", {}).get(popup_name)
        if not popup_cfg:
            return False

        try:
            el = self._page.wait_for_selector(
                popup_cfg["selector"],
                timeout=popup_cfg.get("timeout_ms", 3000),
            )
            if el:
                close_sel = popup_cfg.get("close_selector") or popup_cfg.get(
                    "confirm_selector"
                )
                if close_sel:
                    close_btn = self._page.query_selector(close_sel)
                    if close_btn:
                        close_btn.click()
                        log.info(f"[INIT] 已关闭弹窗: {popup_name}")
                        return True
        except Exception:
            pass
        return False

    # ── Anti-detection delays ──

    def _random_delay(self, min_sec: float = 1, max_sec: float = 3) -> None:
        """Random delay between operations."""
        time.sleep(random.uniform(min_sec, max_sec))

    def _operation_delay(self) -> None:
        """Standard operation interval (from limits config: 5-15s)."""
        time.sleep(random.uniform(5, 15))
