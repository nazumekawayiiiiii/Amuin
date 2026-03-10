"""Mark tool — mark a job as "not suitable" with feedback.

Clicks the "不合适" button, selects a reason, and confirms.
Used when Evaluator decides to skip a job.
"""

import logging

from patchright.sync_api import Page

from .base_tool import BaseTool, SelectorNotFoundError


log = logging.getLogger("claw")

# Default reason to select when marking as not suitable
DEFAULT_REASON = "wrong_position"


class MarkTool(BaseTool):
    """Mark a job as not suitable on the detail panel."""

    def mark(self, reason: str = DEFAULT_REASON) -> bool:
        """Click "不合适" and submit a reason.

        Args:
            reason: Reason key from tool_config reason_selectors
                    (e.g., "wrong_position", "salary", "boss_inactive").

        Returns:
            True if feedback was submitted successfully.
        """
        # Click "不合适" button
        try:
            self._click("not_suitable_button", timeout=3000)
        except SelectorNotFoundError:
            log.warning("[WORK] 不合适按钮未找到")
            return False

        self._random_delay(0.5, 1)

        # Wait for reasons API
        try:
            self._wait_for_api("reasons", timeout=5000)
        except Exception:
            log.warning("[WORK] 不合适原因API超时")
            self._close_feedback_dialog()
            return False

        self._random_delay(0.3, 0.6)

        # Select the reason
        reason_selectors = self._config.get("reason_selectors", {})
        reason_sel = reason_selectors.get(reason)
        if not reason_sel:
            log.warning(f"[WORK] 未知不合适原因: {reason}")
            self._close_feedback_dialog()
            return False

        try:
            reason_el = self._page.wait_for_selector(reason_sel, timeout=3000)
            if reason_el:
                reason_el.click()
                self._random_delay(0.3, 0.5)
        except Exception:
            log.warning(f"[WORK] 原因选项未找到: {reason}")
            self._close_feedback_dialog()
            return False

        # Click confirm
        try:
            self._click("feedback_confirm", timeout=3000)
        except SelectorNotFoundError:
            self._close_feedback_dialog()
            return False

        # Wait for save API
        try:
            self._wait_for_api("save", timeout=5000)
            log.info(f"[WORK] 标记不合适 → 成功 (原因: {reason})")
            return True
        except Exception:
            log.warning("[WORK] 不合适反馈保存超时")
            return False

    def _close_feedback_dialog(self) -> None:
        """Close the feedback dialog without submitting."""
        try:
            self._click("feedback_close", timeout=2000)
        except SelectorNotFoundError:
            pass
