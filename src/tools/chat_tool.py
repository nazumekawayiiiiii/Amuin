"""Chat tool — initiate conversation with a BOSS.

Clicks "立即沟通", handles various response cases
(success, rate limit, daily limit, headhunter warning),
and manages the greet-boss dialog.
"""

import logging

from patchright.sync_api import Page

from .base_tool import BaseTool, DailyLimitError, SelectorNotFoundError


log = logging.getLogger("claw")


class ChatTool(BaseTool):
    """Initiate chat with a BOSS on the job detail page."""

    def initiate(self, encrypt_job_id: str) -> dict:
        """Click "立即沟通" and handle the response.

        Args:
            encrypt_job_id: The job's encrypt ID for API matching.

        Returns:
            Dict with:
              "success": bool,
              "reason": str (e.g., "ok", "already_chatted",
                             "daily_limit", "failed").
        """
        # Check button text first
        chat_cfg = self._config["elements"]["chat_button"]
        expected = chat_cfg.get("expected_text", "立即沟通")

        try:
            btn = self._find_element("chat_button", timeout=3000)
        except SelectorNotFoundError:
            return {"success": False, "reason": "button_not_found"}

        btn_text = (btn.inner_text() or "").strip()
        if btn_text != expected:
            log.info(f"[WORK] 已沟通过，跳过: button={btn_text}")
            return {"success": False, "reason": "already_chatted"}

        # Click the button and wait for API response
        api_cfg = self._config["api_endpoints"]["add_friend"]
        try:
            with self._page.expect_response(
                lambda resp: (
                    api_cfg["url_pattern"] in resp.url
                    and f"jobId={encrypt_job_id}" in resp.url
                ),
                timeout=15000,
            ) as response_info:
                btn.click()
                self._random_delay(0.5, 1)

            return self._handle_response(response_info.value)
        except Exception as e:
            log.error(f"[WORK] 沟通API超时: {e}")
            return {"success": False, "reason": "timeout"}

    def _handle_response(self, response) -> dict:
        """Parse the add-friend API response and handle edge cases."""
        try:
            data = response.json()
        except Exception:
            return {"success": False, "reason": "invalid_json"}

        code = data.get("code")
        resp_codes = self._config.get("response_codes", {})

        # Success
        if code == resp_codes.get("success", 0):
            self._close_greet_dialog()
            log.info("[WORK] 发起沟通 → 成功")
            return {"success": True, "reason": "ok"}

        # Check bizData for rate limit / daily limit
        zp_data = data.get("zpData", {})
        biz_code = zp_data.get("bizCode")
        dialog_data = zp_data.get("bizData", {}).get("chatRemindDialog", {})
        content = dialog_data.get("content", "")
        block_level = dialog_data.get("blockLevel")

        if biz_code == resp_codes.get("rate_limit_biz_code", 1):
            daily_text = resp_codes.get("daily_limit_text", "")
            if daily_text and (daily_text in content or "明天再来" in content):
                log.error("[WORK] 今日沟通次数已用完")
                raise DailyLimitError("今日沟通次数已用完")

            # Rate limit warning but still has chances
            if block_level == 0 and "沟通机会" in content:
                self._click_chat_block_confirm()
                self._close_greet_dialog()
                log.info("[WORK] 发起沟通 → 成功（有次数限制提醒）")
                return {"success": True, "reason": "rate_warned"}

            # Headhunter warning
            if "猎头" in content:
                self._click_continue_button()
                self._close_greet_dialog()
                log.info("[WORK] 发起沟通 → 成功（猎头提醒）")
                return {"success": True, "reason": "headhunter_warned"}

        log.warning(f"[WORK] 沟通失败: code={code}, content={content}")
        return {"success": False, "reason": f"api_error_{code}"}

    def _close_greet_dialog(self) -> None:
        """Close the greet-boss dialog that appears after starting chat."""
        self._random_delay(0.5, 1)
        self.close_popup("greet_boss_dialog")

    def _click_chat_block_confirm(self) -> None:
        """Click confirm on the chat-block rate limit dialog."""
        self._random_delay(0.3, 0.5)
        self.close_popup("chat_block_dialog")
        self._random_delay(0.5, 1)

    def _click_continue_button(self) -> None:
        """Click the continue button on headhunter warning dialog."""
        try:
            xpath = (
                'xpath=//*[contains(@class, "chat-block-dialog")]'
                '//*[contains(@class, "chat-block-footer")]'
                '//*[contains(text(), "继续")]'
            )
            btn = self._page.wait_for_selector(xpath, timeout=3000)
            if btn:
                btn.click()
        except Exception:
            pass
        self._random_delay(0.5, 1)
