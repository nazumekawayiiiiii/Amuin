"""Patchright browser lifecycle management.

Uses Patchright (anti-detection Playwright fork) with persistent context
for maximum stealth against BOSS直聘's anti-bot system.

Key differences from standard Playwright:
  - launch_persistent_context: uses a real Chrome user-data-dir
  - channel="chrome": uses system Chrome instead of Chromium
  - No stealth plugins needed: Patchright patches automation signals at the binary level
"""

import shutil
import logging
from pathlib import Path

from patchright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    Playwright,
)

log = logging.getLogger("claw")


class BrowserManager:
    """Manages a Patchright Chromium instance with persistent context.

    Usage:
        mgr = BrowserManager(data_dir="./data")
        page = mgr.start()
        # ... use page ...
        mgr.stop()

    Login state persists automatically via user_data_dir.
    No manual storage_state management needed.
    """

    def __init__(self, data_dir: str | Path):
        self._data_dir = Path(data_dir)
        self._profile_dir = self._data_dir / "browser_profile"
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def start(self) -> Page:
        """Launch browser with persistent context.

        Uses system Chrome (channel="chrome") for better anti-detection.
        Login state from previous sessions is automatically restored
        via the user_data_dir.

        Returns:
            The main Page instance.
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        self._launch_context()
        return self._page

    def restart_clean(self) -> Page:
        """Destroy and recreate browser with a fresh profile.

        Used when the saved profile is corrupted and prevents
        normal page loading. Deletes the profile directory entirely,
        then launches a fresh instance.

        Returns:
            A new Page instance.
        """
        log.info("[INIT] 重建浏览器 (清除旧配置)...")

        # Close existing context
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None

        # Delete the entire profile directory
        try:
            if self._profile_dir.exists():
                shutil.rmtree(self._profile_dir, ignore_errors=True)
        except Exception:
            pass

        self._launch_context()
        return self._page

    def _launch_context(self) -> None:
        """Launch persistent context with anti-detection settings."""
        self._profile_dir.mkdir(parents=True, exist_ok=True)

        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-port=9222",  # CDP 端口，供验证项目附着
            ],
        )

        # Persistent context opens a blank page automatically
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

    def logout(self) -> None:
        """Clear all login state (cookies + storage) from the profile.

        If browser is running, uses Patchright API.
        Otherwise, deletes cookie/storage files from the profile directory.
        """
        if self._context:
            # Browser running — use API
            try:
                self._context.clear_cookies()
            except Exception:
                pass
            if self._page:
                try:
                    self._page.evaluate("localStorage.clear()")
                    self._page.evaluate("sessionStorage.clear()")
                except Exception:
                    pass
            log.info("[INIT] 已清除登录状态")
        else:
            # Browser not running — remove files from profile
            default_dir = self._profile_dir / "Default"
            if default_dir.exists():
                for name in ("Cookies", "Cookies-journal"):
                    target = default_dir / name
                    if target.exists():
                        target.unlink(missing_ok=True)
                for name in ("Local Storage", "Session Storage"):
                    target = default_dir / name
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
            log.info("[INIT] 已清除登录状态（离线）")

    def save_storage_state(self) -> None:
        """No-op: persistent context auto-saves to user_data_dir."""
        pass

    def stop(self) -> None:
        """Clean shutdown of all browser resources."""
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

        self._playwright = None
        self._context = None
        self._page = None

    @property
    def page(self) -> Page | None:
        return self._page

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    @property
    def is_running(self) -> bool:
        return self._context is not None

    @property
    def is_alive(self) -> bool:
        """Check if browser is still responsive (not manually closed)."""
        if not self._context or not self._page:
            return False
        try:
            self._page.evaluate("1")
            return True
        except Exception:
            return False
