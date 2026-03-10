"""Search tool — navigate to jobs page and search by keyword.

Handles keyword input, search triggering (Enter key),
and waiting for results to load.
"""

import logging

from patchright.sync_api import Page

from .base_tool import BaseTool


log = logging.getLogger("claw")


class SearchTool(BaseTool):
    """Search for jobs by keyword on BOSS直聘."""

    def ensure_on_jobs_page(self) -> None:
        """Navigate to the recommend/jobs page if not already there.

        Filters must be applied on this page (Vue $children + ka attributes
        only exist here, not on the search results page).
        """
        url = self._config.get("url", "")
        if url and not self._page.url.startswith(url):
            try:
                self._page.goto(url, wait_until="domcontentloaded")
            except Exception as e:
                log.warning(f"[WORK] 导航异常: {e}")
            self._random_delay(2, 4)

        self.check_error_page()
        self.close_popup("security_question")

    def search(self, keyword: str) -> bool:
        """Input keyword and trigger search.

        Assumes we are already on the jobs page (call ensure_on_jobs_page
        first, and apply filters before searching).

        Args:
            keyword: Search keyword (e.g., "Python开发").

        Returns:
            True if search results loaded, False if empty or failed.
        """

        # Check if already showing results for this keyword
        current_query = self._extract_vue_data("current_query")
        if current_query == keyword.strip():
            log.info(f"[WORK] 搜索关键词未变: {keyword}")
            return True

        # Type keyword
        self._fill("search_box", keyword.strip())
        self._random_delay(0.3, 0.8)
        log.info(f"[WORK] 搜索: {keyword}")

        # Press Enter and wait for API response
        try:
            with self._page.expect_response(
                lambda resp: (
                    self._config["api_endpoints"]["search_list"]["url_pattern"]
                    in resp.url
                    or self._config["api_endpoints"]["recommend_list"]["url_pattern"]
                    in resp.url
                ),
                timeout=15000,
            ) as response_info:
                self._page.keyboard.press("Enter")

            data = response_info.value.json()
            if data.get("code") != 0:
                log.warning(f"[WORK] 搜索API返回异常: code={data.get('code')}")
                return False
        except Exception as e:
            log.error(f"[WORK] 搜索响应超时: {e}")
            return False

        # Wait for loading to finish
        self._wait_for_loading()
        return True

    def get_job_list(self) -> list[dict]:
        """Get current job list from Vue instance."""
        jobs = self._extract_vue_data("job_list")
        return jobs if isinstance(jobs, list) else []

    def has_more(self) -> bool:
        """Check if there are more pages of results."""
        return bool(self._extract_vue_data("has_more"))

    def scroll_for_next_page(self) -> bool:
        """Scroll down to trigger loading the next page of results.

        Returns:
            True if scroll completed (may have triggered load).
        """
        wait_cfg = self._config.get("wait_selectors", {})
        container_sel = wait_cfg.get("job_list_container", "")
        if not container_sel:
            return False

        container = self._page.query_selector(container_sel)
        if not container:
            return False

        bbox = container.bounding_box()
        if not bbox:
            return False

        # Move mouse to list area
        inner_h = self._page.evaluate("window.innerHeight")
        self._page.mouse.move(
            bbox["x"] + bbox["width"] / 2,
            inner_h / 2,
        )

        # Scroll with random increments
        for _ in range(15):
            increment = 40 + int(30 * (0.5 + 0.5 * __import__("random").random()))
            self._page.mouse.wheel(0, increment)
            self._random_delay(0.08, 0.15)

        self._random_delay(1, 2)
        return True

    def _wait_for_loading(self) -> None:
        """Wait for the loading indicator to disappear."""
        wait_cfg = self._config.get("wait_selectors", {})
        loading_sel = wait_cfg.get("loading")
        if loading_sel:
            try:
                self._page.wait_for_function(
                    f'!document.querySelector("{loading_sel}")',
                    timeout=10000,
                )
            except Exception:
                pass
        self._random_delay(0.5, 1.5)
