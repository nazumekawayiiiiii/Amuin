"""Job detail tool — click into job detail and extract full JD data.

Reads complete job information from the Vue instance on the detail
side panel, including job description, boss info, and company info.
"""

import logging
from typing import Any

from patchright.sync_api import Page

from .base_tool import BaseTool, SelectorNotFoundError


log = logging.getLogger("claw")


class JobDetailTool(BaseTool):
    """Click a job card and extract full detail from Vue instance."""

    def click_job(self, index: int) -> dict | None:
        """Click the job card at the given index and wait for detail to load.

        Args:
            index: Zero-based index in the current job list.

        Returns:
            Full job detail dict, or None if failed.
        """
        # Scroll the target card into view
        scroll_js = f"""(() => {{
            const list = document.querySelector("ul.rec-job-list");
            if (!list || !list.children[{index}]) return false;
            list.children[{index}].scrollIntoView({{
                behavior: 'smooth',
                block: Math.random() > 0.5 ? 'center' : 'end'
            }});
            return true;
        }})()"""

        if not self._page.evaluate(scroll_js):
            log.warning(f"[WORK] 职位卡片不存在: index={index}")
            return None

        self._random_delay(0.3, 0.8)

        # Query card elements and API config
        api_cfg = self._config["api_endpoints"]["detail_loaded"]
        cards = self._page.query_selector_all(
            self._config["elements"]["job_cards"]["selector"]
        )
        if index >= len(cards):
            log.warning(f"[WORK] 职位卡片索引越界: {index}/{len(cards)}")
            return None

        # Click the card and wait for detail API
        try:
            with self._page.expect_response(
                lambda resp: api_cfg["url_pattern"] in resp.url,
                timeout=10000,
            ) as response_info:
                cards[index].click()

            if response_info.value.status != 200:
                log.warning(f"[WORK] 详情API状态: {response_info.value.status}")
                return None
        except Exception as e:
            log.warning(f"[WORK] 详情加载超时: {e}")
            return None

        self._random_delay(0.5, 1)

        # Extract data from Vue instance
        return self.get_detail()

    def get_detail(self) -> dict | None:
        """Extract full job detail from the currently displayed detail panel.

        Returns:
            Dict with keys: jobInfo, bossInfo, brandComInfo. Or None.
        """
        data = self._extract_vue_data("job_detail")
        if not data or not isinstance(data, dict):
            log.warning("[WORK] 无法读取职位详情 Vue 数据")
            return None
        return data

    def get_chat_button_text(self) -> str | None:
        """Get the text of the chat button (e.g., "立即沟通" or "继续沟通").

        Returns None if button not found.
        """
        sel = ".job-detail-box .op-btn.op-btn-chat"
        try:
            text = self._page.evaluate(
                f'document.querySelector("{sel}")?.innerHTML?.trim()'
            )
            return text
        except Exception:
            return None

    @staticmethod
    def parse_salary(salary_desc: str) -> tuple[float, float, int] | None:
        """Parse salary string like '15-25K' or '15-25K·13薪'.

        Returns:
            (min_k, max_k, months) or None if unparseable.
        """
        if not salary_desc:
            return None
        import re

        base = re.match(r"([\d.]+)-([\d.]+)[kK]", salary_desc)
        if not base:
            return None
        min_k = float(base.group(1))
        max_k = float(base.group(2))

        month_match = re.search(r"(\d+)薪", salary_desc)
        months = int(month_match.group(1)) if month_match else 12

        return (min_k, max_k, months)
