"""Reply tool — send pre-generated follow-up messages.

Navigates to chat page, finds the target conversation,
types and sends a template message. No LLM involved.
Used by the follow-up panel for one-click sending.
"""

import time
import logging

from patchright.sync_api import Page

from .base_tool import (
    BaseTool, SelectorNotFoundError,
    evaluate_main_world, wait_for_main_world,
)

log = logging.getLogger("claw")


class ReplyTool(BaseTool):
    """Send a message in an existing chat conversation."""

    def send_message(
        self, encrypt_job_id: str, message: str
    ) -> bool:
        """Find a conversation and send a message.

        Args:
            encrypt_job_id: The job's encrypt ID to find the conversation.
            message: Text to send.

        Returns:
            True if message was sent successfully.
        """
        chat_url = self._common.get("urls", {}).get("chat_page", "")
        if chat_url and "chat" not in self._page.url:
            self._page.goto(chat_url, wait_until="domcontentloaded")
            self._random_delay(2, 3)

        # Wait for friend list
        friend_list_ready = self._wait_for_friend_list()
        if not friend_list_ready:
            log.error("[WORK] 会话列表加载失败")
            return False

        # Find and click the target conversation
        if not self._click_conversation(encrypt_job_id):
            log.warning(f"[WORK] 未找到会话: {encrypt_job_id}")
            return False

        self._random_delay(1, 2)

        # Type and send message
        return self._type_and_send(message)

    def _wait_for_friend_list(self) -> bool:
        """Wait for the chat friend list to load."""
        extract = self._config.get("data_extracts", {}).get("friend_list", {})
        mount = extract.get("mount_selector", ".main-wrap .chat-user")
        path = extract.get("path", "__vue__.list")

        # Poll via main-world bridge (wait_for_function can't see __vue__)
        js = f"""Array.isArray(
            document.querySelector('{mount}')?.{path}
        )"""
        return bool(wait_for_main_world(self._page, js, timeout=10000))

    def _click_conversation(self, encrypt_job_id: str) -> bool:
        """Find and click the conversation matching the job ID."""
        items_sel = self._config["elements"]["chat_list_items"]["selector"]

        # Use Vue data to find the right li element (main-world bridge)
        clicked = evaluate_main_world(
            self._page,
            f"""(() => {{
                const items = document.querySelectorAll('{items_sel}');
                for (const item of items) {{
                    if (item.__vue__?.source?.encryptJobId === '{encrypt_job_id}') {{
                        item.click();
                        return true;
                    }}
                }}
                return false;
            }})()""",
        )

        if clicked:
            # Wait for chat history to load
            api_cfg = self._config.get("api_endpoints", {}).get("history_msg", {})
            if api_cfg:
                try:
                    self._page.wait_for_response(
                        lambda r: api_cfg["url_pattern"] in r.url,
                        timeout=5000,
                    )
                except Exception:
                    pass
            return True

        # If not visible, try scrolling the list to find it
        return self._scroll_and_find(encrypt_job_id, items_sel)

    def _scroll_and_find(self, encrypt_job_id: str, items_sel: str) -> bool:
        """Scroll the chat list to find a conversation not currently visible."""
        scroll_mount = ".chat-content .user-list .user-list-content"

        for _ in range(10):  # Max 10 scroll attempts
            evaluate_main_world(
                self._page,
                f"document.querySelector('{scroll_mount}')?.__vue__?.scrollToBottom()",
            )
            self._random_delay(0.5, 1)

            # Check if target appeared (main-world bridge for __vue__ access)
            found = evaluate_main_world(
                self._page,
                f"""(() => {{
                    const items = document.querySelectorAll('{items_sel}');
                    for (const item of items) {{
                        if (item.__vue__?.source?.encryptJobId === '{encrypt_job_id}') {{
                            item.click();
                            return true;
                        }}
                    }}
                    return false;
                }})()""",
            )
            if found:
                return True

            # Check if we've reached the end of the list
            at_end = self._page.evaluate(
                f"""(() => {{
                    const el = document.querySelector(
                        '{scroll_mount} div[role=tfoot] .finished'
                    );
                    return el?.textContent?.includes('没有') ?? false;
                }})()"""
            )
            if at_end:
                break

        return False

    def _type_and_send(self, message: str) -> bool:
        """Type a message and click send."""
        try:
            chat_input = self._find_element("chat_input", timeout=5000)
        except SelectorNotFoundError:
            log.error("[WORK] 聊天输入框未找到")
            return False

        chat_input.click()
        chat_input.type(message, delay=50)
        self._random_delay(0.3, 0.5)

        try:
            self._click("send_button", timeout=3000)
            log.info(f"[WORK] 发送消息成功: {message[:30]}...")
            self._random_delay(0.5, 1)
            return True
        except SelectorNotFoundError:
            log.error("[WORK] 发送按钮未找到或不可用")
            return False
