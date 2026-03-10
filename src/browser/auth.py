"""Login state management for BOSS直聘.

Handles login detection and QR code login flow.
With Patchright persistent context, login state auto-persists in user_data_dir.

Flow:
  1. Navigate to zhipin.com home
  2. Check for avatar → logged in
  3. If not: restart browser with clean profile, navigate to login page, wait for QR
  4. After scan: avatar appears → done (state auto-saved by persistent context)
"""

import logging
import time
import threading
from pathlib import Path

from patchright.sync_api import Page

from ..tools.base_tool import load_tool_config

log = logging.getLogger("claw")

# Selector for detecting logged-in state (user avatar in header nav).
LOGGED_IN_SELECTOR = ".user-nav .nav-figure"

# Max wait time for user to scan QR code (5 minutes)
LOGIN_TIMEOUT_SEC = 300

# How often to check login status while waiting for QR scan
LOGIN_POLL_INTERVAL_SEC = 3


class AuthManager:
    """Manages login state for BOSS直聘."""

    def __init__(self, page: Page, data_dir: str | Path, browser_mgr=None,
                 stop_event: threading.Event | None = None):
        self._page = page
        self._data_dir = Path(data_dir)
        self._browser_mgr = browser_mgr
        self._stop_event = stop_event or threading.Event()

        config = load_tool_config()
        urls = config.get("common", {}).get("urls", {})
        self._login_url = urls.get(
            "login_page", "https://www.zhipin.com/web/user/"
        )

    def ensure_logged_in(self) -> bool:
        """Single entry point: check login → prompt if needed → return result."""

        if self._stop_event.is_set():
            return False

        # Step 1: Try navigating to zhipin.com
        log.info("[INIT] 检查登录状态...")
        if not self._navigate("https://www.zhipin.com"):
            if self._stop_event.is_set():
                return False
            # Navigation failed (about:blank) — corrupted browser profile
            log.warning("[INIT] 页面加载失败，清理浏览器配置后重试...")
            if not self._restart_clean():
                return False
            # Retry navigation with clean context
            if not self._navigate("https://www.zhipin.com"):
                log.error("[INIT] 清理后仍无法加载页面")
                return False

        # Step 2: Check for avatar
        if self._has_avatar():
            log.info("[INIT] 登录态有效")
            return True

        # Step 3: Not logged in — navigate to login page directly.
        # Do NOT restart_clean here: profile cookies are fine, just expired.
        # restart_clean deletes the entire profile dir, which is only needed
        # when the profile is corrupted (Step 1 failure).
        log.info("[INIT] 登录态失效，需要重新登录")
        return self._wait_for_qr_login()

    def _navigate(self, url: str) -> bool:
        """Navigate to a URL and verify the page actually loaded.

        Returns:
            True if page loaded (URL is not about:blank), False otherwise.
        """
        if self._stop_event.is_set():
            return False
        try:
            self._page.goto(url, wait_until="load", timeout=15_000)
        except Exception as e:
            log.warning(f"[INIT] 导航异常: {e}")

        # Let the page settle (interruptible)
        for _ in range(4):
            if self._stop_event.is_set():
                return False
            time.sleep(0.5)

        try:
            current = self._page.url
        except Exception:
            return False
        if not current or "about:blank" in current:
            return False
        return True

    def _has_avatar(self) -> bool:
        """Check if the logged-in avatar selector is present."""
        try:
            el = self._page.wait_for_selector(
                LOGGED_IN_SELECTOR, timeout=5_000
            )
            return el is not None
        except Exception:
            return False

    def _restart_clean(self) -> bool:
        """Restart browser with a clean profile."""
        if not self._browser_mgr:
            log.error("[INIT] 无法重建浏览器上下文")
            return False
        try:
            self._page = self._browser_mgr.restart_clean()
            return True
        except Exception as e:
            log.error(f"[INIT] 重建浏览器上下文失败: {e}")
            return False

    def _wait_for_qr_login(self) -> bool:
        """Navigate to login page and poll until QR scan completes."""
        log.info("[INIT] 请在浏览器中扫码登录（5分钟超时）...")

        self._page.bring_to_front()
        if not self._navigate(self._login_url):
            if self._stop_event.is_set():
                return False
            log.error("[INIT] 登录页无法加载")
            return False

        log.info(f"[INIT] 登录页已加载: {self._page.url}")

        # Poll for avatar (login success indicator)
        elapsed = 0
        while elapsed < LOGIN_TIMEOUT_SEC:
            # Interruptible sleep (check stop every 0.5s)
            for _ in range(LOGIN_POLL_INTERVAL_SEC * 2):
                if self._stop_event.is_set():
                    log.info("[INIT] 登录等待被用户中断")
                    return False
                time.sleep(0.5)
            elapsed += LOGIN_POLL_INTERVAL_SEC

            # Quick check: avatar visible?
            try:
                el = self._page.query_selector(LOGGED_IN_SELECTOR)
                if el and el.is_visible():
                    self._save_state()
                    return True
            except Exception:
                pass

            # Periodic log
            if elapsed % 30 == 0:
                remaining = LOGIN_TIMEOUT_SEC - elapsed
                log.info(f"[INIT] 等待扫码中... (剩余 {remaining} 秒)")

        log.error("[INIT] 登录超时（5分钟）")
        return False

    def _save_state(self) -> None:
        """Log successful login. State auto-persists via persistent context."""
        log.info("[INIT] 登录成功（状态自动保存）")
