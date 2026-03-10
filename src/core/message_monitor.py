"""Message monitor — periodic check for new HR messages.

No LLM. Pure Playwright navigation + Vue data reading + notification callback.
Runs on a timer (default every 30 minutes).
"""

import logging
import time
from typing import Callable

from patchright.sync_api import Page

from ..tools.base_tool import (
    load_tool_config, AccessDeniedError,
    evaluate_main_world, wait_for_main_world,
)
from ..db.models import ConversationStore

log = logging.getLogger("claw")

# MsgStatus from geekgeekrun
MSG_STATUS_BOSS = 0       # Message from boss or system
MSG_STATUS_NOT_READ = 1   # Self message not read by boss
MSG_STATUS_READ = 2       # Self message read by boss
MSG_STATUS_REVOKED = 3    # Message revoked


class MessageMonitor:
    """Monitors BOSS直聘 chat page for new messages.

    Checks the friend list on the chat page, compares with
    known conversation states in DB, and triggers notifications
    when HR replies are detected.

    Usage:
        monitor = MessageMonitor(page, store, on_new_message=callback)
        monitor.check_once()
        # or run periodically:
        monitor.run(interval_minutes=30)
    """

    def __init__(
        self,
        page: Page,
        store: ConversationStore,
        on_new_message: Callable[[dict], None] | None = None,
    ):
        self._page = page
        self._store = store
        self._on_new_message = on_new_message
        self._config = load_tool_config()
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def check_once(self) -> list[dict]:
        """Navigate to chat page, scan for new messages.

        Returns:
            List of dicts with new message info:
            [{"company": ..., "position": ..., "boss_name": ..., ...}]
        """
        chat_url = self._config["common"]["urls"]["chat_page"]
        reply_cfg = self._config.get("reply_tool", {})

        log.info("[MSG] 检查新消息...")
        self._page.goto(chat_url, wait_until="domcontentloaded")
        time.sleep(3)

        # Wait for friend list to load
        friend_list = self._wait_for_friend_list(reply_cfg)
        if friend_list is None:
            log.warning("[MSG] 无法读取会话列表")
            return []

        new_messages = []
        for friend in friend_list:
            encrypt_job_id = friend.get("encryptJobId", "")
            if not encrypt_job_id:
                continue

            unread = friend.get("unreadCount", 0)
            last_is_self = friend.get("lastIsSelf", True)

            # New message from boss: unread > 0 and last message is not from self
            if unread > 0 and not last_is_self:
                msg_info = {
                    "encrypt_job_id": encrypt_job_id,
                    "encrypt_boss_id": friend.get("encryptBossId", ""),
                    "boss_name": friend.get("name", ""),
                    "company": friend.get("brandName", ""),
                    "position": friend.get("sourceTitle", ""),
                    "last_text": friend.get("lastText", ""),
                    "unread_count": unread,
                }
                new_messages.append(msg_info)

                # Update DB
                self._store.update_message_status(encrypt_job_id, "boss")

                # Trigger notification
                if self._on_new_message:
                    self._on_new_message(msg_info)

                log.info(
                    f"[MSG] 新消息: {msg_info['company']} - "
                    f"{msg_info['boss_name']}: {msg_info['last_text'][:30]}"
                )

        if not new_messages:
            log.info("[MSG] 无新消息")

        return new_messages

    def run(self, interval_minutes: int = 30) -> None:
        """Run periodic message checks.

        Blocks the calling thread. Call stop() to exit.
        """
        log.info(f"[MSG] 消息监控启动 (每 {interval_minutes} 分钟)")
        while not self._stopped:
            try:
                self.check_once()
            except AccessDeniedError as e:
                log.error(f"[MSG] 风控拦截: {e}")
                break
            except Exception as e:
                log.error(f"[MSG] 检查失败: {e}")

            # Wait for next check
            for _ in range(interval_minutes * 60):
                if self._stopped:
                    break
                time.sleep(1)

        log.info("[MSG] 消息监控已停止")

    def _wait_for_friend_list(self, reply_cfg: dict) -> list[dict] | None:
        """Wait for friend list Vue data to be available."""
        extract = reply_cfg.get("data_extracts", {}).get("friend_list", {})
        mount = extract.get("mount_selector", ".main-wrap .chat-user")
        path = extract.get("path", "__vue__.list")

        # Poll via main-world bridge (wait_for_function can't see __vue__)
        js = f"""Array.isArray(
            document.querySelector('{mount}')?.{path}
        )"""
        ready = wait_for_main_world(self._page, js, timeout=10000)
        if not ready:
            return None

        return evaluate_main_world(
            self._page,
            f"document.querySelector('{mount}')?.{path}",
        )
