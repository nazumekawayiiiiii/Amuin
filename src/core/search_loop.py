"""Search loop — the main job application cycle.

Wires together all Phase 1 components:
  search_tool → filter_tool → job_detail_tool → evaluator → chat_tool / mark_tool

Processes one combination (keyword + filters) at a time.
The outer scheduler calls this loop for each combination in the Cartesian product.
"""

import logging
import random
import threading
import time
from typing import Callable

from patchright.sync_api import Page

from ..tools.base_tool import (
    load_tool_config,
    AccessDeniedError,
    SecurityCheckError,
    DailyLimitError,
)
from ..tools.search_tool import SearchTool
from ..tools.filter_tool import FilterTool
from ..tools.job_detail_tool import JobDetailTool
from ..tools.chat_tool import ChatTool
from ..tools.mark_tool import MarkTool
from ..agents.llm_client import LLMClient
from ..agents.evaluator import Evaluator, EvaluationResult


log = logging.getLogger("claw")


class SearchLoopStats:
    """Tracks statistics for a single search loop run."""

    def __init__(self):
        self.browsed = 0
        self.applied = 0
        self.skipped = 0
        self.errors = 0
        self.deduped = 0

    def summary(self) -> str:
        return (
            f"浏览 {self.browsed}, 投递 {self.applied}, "
            f"跳过 {self.skipped}, 去重 {self.deduped}, 错误 {self.errors}"
        )


class SearchLoop:
    """Execute one full search loop for a given keyword + filter combination.

    Usage:
        loop = SearchLoop(page, config, evaluator, ...)
        stats = loop.run(keyword="Python开发", filters={"salary": "10-20K"})
    """

    def __init__(
        self,
        page: Page,
        evaluator: Evaluator,
        known_job_ids: set[str] | None = None,
        on_match: Callable[[dict, EvaluationResult], None] | None = None,
        on_skip: Callable[[dict, EvaluationResult], None] | None = None,
        daily_limit: int = 40,
        daily_count: int = 0,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        preferences: dict | None = None,
    ):
        """
        Args:
            page: Shared Playwright page.
            evaluator: Evaluator agent instance.
            known_job_ids: Set of job_encrypt_ids already processed (for dedup).
            on_match: Callback when a job is matched (for DB persistence).
            on_skip: Callback when a job is skipped (for DB persistence).
            daily_limit: Max daily applications.
            daily_count: Current daily application count.
            stop_event: Threading event to signal stop from UI.
            pause_event: Threading event to signal pause from UI.
            preferences: User preferences (company_blacklist, keyword_blacklist).
        """
        self._config = load_tool_config()
        common = self._config.get("common", {})
        filter_codes = self._config.get("filter_codes", {})

        self._search = SearchTool(page, self._config["search_tool"], common)
        self._filter = FilterTool(page, self._config["filter_tool"], common, filter_codes)
        self._detail = JobDetailTool(page, self._config["job_detail_tool"], common)
        self._chat = ChatTool(page, self._config["chat_tool"], common)
        self._mark = MarkTool(page, self._config["mark_tool"], common)

        self._evaluator = evaluator
        self._known_ids = known_job_ids or set()
        self._on_match = on_match
        self._on_skip = on_skip
        self._daily_limit = daily_limit
        self._daily_count = daily_count
        self._page = page

        # UI signals
        self._stop_event = stop_event or threading.Event()
        self._pause_event = pause_event or threading.Event()

        # Blacklists
        prefs = preferences or {}
        self._company_blacklist = [
            s.lower() for s in prefs.get("company_blacklist", [])
        ]
        self._keyword_blacklist = [
            s.lower() for s in prefs.get("keyword_blacklist", [])
        ]

    @property
    def _stopped(self) -> bool:
        """Check if stop has been signalled (UI event or internal)."""
        return self._stop_event.is_set()

    def stop(self) -> None:
        """Signal the loop to stop after current job."""
        self._stop_event.set()

    def _wait_if_paused(self) -> None:
        """Block while pause event is set. Returns when unpaused or stopped.

        Also checks browser liveness every few seconds — if the user
        manually closed the browser window while paused, trigger stop
        instead of waiting forever.
        """
        ticks = 0
        while self._pause_event.is_set() and not self._stopped:
            time.sleep(0.5)
            ticks += 1
            # Check browser every 5 seconds (10 ticks × 0.5s)
            if ticks % 10 == 0:
                try:
                    self._page.evaluate("1")
                except Exception:
                    log.warning("[WORK] 暂停期间浏览器已关闭，自动停止")
                    self._stop_event.set()
                    break

    def run(
        self, keyword: str, filters: dict[str, str] | None = None
    ) -> SearchLoopStats:
        """Run the search loop for one keyword + filter combination.

        Args:
            keyword: Search keyword.
            filters: Optional filter dict, e.g., {"salary": "10-20K"}.

        Returns:
            Statistics for this run.
        """
        stats = SearchLoopStats()
        filters = filters or {}

        log.info(f"[WORK] === 开始组合: {keyword} + {filters} ===")

        # 1. Ensure on recommend page (filters only work here)
        self._search.ensure_on_jobs_page()

        # 2. Apply filters BEFORE search (Vue $children + ka attributes
        #    only exist on the recommend page, not on search results page)
        if filters:
            filter_results = self._filter.apply_filters(filters)
            applied = sum(1 for v in filter_results.values() if v)
            total = len(filter_results)
            if total > 0 and applied == 0:
                log.warning(f"[WORK] 所有筛选条件均失败 ({total}项)，跳过此组合")
                return stats
            elif applied < total:
                failed = [k for k, v in filter_results.items() if not v]
                log.warning(f"[WORK] 部分筛选失败: {failed}")
            self._random_delay(1, 3)

        # 3. Search keyword (filter params carry over via URL)
        if not self._search.search(keyword):
            log.warning("[WORK] 搜索无结果，跳过此组合")
            return stats

        # 4. Process pages
        consecutive_empty_pages = 0
        max_empty_pages = 3

        while not self._stopped:
            # Check daily limit
            if self._daily_count >= self._daily_limit:
                log.info(f"[WORK] 今日投递达上限: {self._daily_count}/{self._daily_limit}")
                break

            # Get current job list
            job_list = self._search.get_job_list()
            if not job_list:
                log.info("[WORK] 职位列表为空，组合耗尽")
                break

            # Filter out already-processed jobs
            new_jobs = []
            for i, job in enumerate(job_list):
                job_id = job.get("encryptJobId", "")
                if job_id and job_id not in self._known_ids:
                    new_jobs.append((i, job))
                else:
                    stats.deduped += 1

            if not new_jobs:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= max_empty_pages:
                    log.info(f"[WORK] 连续 {max_empty_pages} 页无新职位，提前退出")
                    break
                # Scroll for next page
                if self._search.has_more():
                    self._search.scroll_for_next_page()
                    self._random_delay(2, 4)
                    continue
                else:
                    log.info("[WORK] hasMore=false，组合耗尽")
                    break

            consecutive_empty_pages = 0
            consecutive_errors = 0

            # Process each new job
            for list_index, job_data in new_jobs:
                if self._stopped:
                    break
                if self._daily_count >= self._daily_limit:
                    break

                # Pause support — block here until unpaused
                self._wait_if_paused()
                if self._stopped:
                    break

                job_id = job_data.get("encryptJobId", "")
                self._known_ids.add(job_id)
                stats.browsed += 1

                try:
                    self._process_job(list_index, job_data, stats)
                    consecutive_errors = 0
                except DailyLimitError:
                    log.info("[WORK] 今日沟通次数已用完")
                    self.stop()
                    break
                except (AccessDeniedError, SecurityCheckError) as e:
                    log.error(f"[ERROR] {e}")
                    self.stop()
                    break
                except Exception as e:
                    log.error(f"[ERROR] 处理职位异常: {e}")
                    stats.errors += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        log.error("[ERROR] 连续3次异常，跳过当前页")
                        break

                # Operation interval
                self._operation_delay()

            # Check if more pages
            if not self._search.has_more():
                log.info("[WORK] hasMore=false，组合耗尽")
                break

            # Scroll for next page
            self._search.scroll_for_next_page()
            self._random_delay(2, 4)

        log.info(f"[WORK] === 组合结束: {stats.summary()} ===")
        return stats

    def _process_job(
        self, list_index: int, job_data: dict, stats: SearchLoopStats
    ) -> None:
        """Process a single job: detail → pre-filter → evaluate → act."""
        job_id = job_data.get("encryptJobId", "")
        company = job_data.get("brandName", "")
        job_name = job_data.get("jobName", job_data.get("jobExperience", ""))

        # 1. Click into detail
        detail = self._detail.click_job(list_index)
        if not detail:
            stats.errors += 1
            return

        job_info = detail.get("jobInfo", {})
        boss_info = detail.get("bossInfo", {})

        # 2. Pre-filter (pure Python, zero LLM)
        skip_reason = self._pre_filter(job_data, detail)
        if skip_reason:
            log.info(f"[WORK] 前置过滤跳过: {company}-{job_name} ({skip_reason})")
            self._mark.mark()
            stats.skipped += 1
            if self._on_skip:
                self._on_skip(detail, None)
            return

        # 3. Check if already chatted
        btn_text = self._detail.get_chat_button_text()
        if btn_text and btn_text != "立即沟通":
            log.info(f"[WORK] 已沟通过: {company}-{job_name}")
            stats.deduped += 1
            return

        # 4. Evaluator (THE LLM call)
        result = self._evaluator.evaluate(detail)
        if not result:
            stats.errors += 1
            return

        # 5. Act on decision
        if result.is_match:
            chat_result = self._chat.initiate(job_info.get("encryptId", job_id))
            if chat_result["success"]:
                self._daily_count += 1
                stats.applied += 1
                log.info(
                    f"[WORK] 投递成功: {company}-{job_name} "
                    f"({result.score}分) [{self._daily_count}/{self._daily_limit}]"
                )
            else:
                stats.errors += 1
                log.warning(
                    f"[WORK] 投递失败: {company}-{job_name} "
                    f"({chat_result['reason']})"
                )
            if self._on_match:
                self._on_match(detail, result)
        else:
            self._mark.mark()
            stats.skipped += 1
            log.info(
                f"[WORK] 跳过: {company}-{job_name} "
                f"({result.score}分, {result.reason})"
            )
            if self._on_skip:
                self._on_skip(detail, result)

    def _pre_filter(self, job_data: dict, detail: dict) -> str | None:
        """Quick pre-filter before LLM evaluation. Returns skip reason or None.

        Checks:
          - Boss activity level
          - Job validity
          - Company blacklist
          - Keyword blacklist (job name + JD text)
        """
        boss_info = detail.get("bossInfo", {})
        brand_info = detail.get("brandComInfo", {})
        job_info = detail.get("jobInfo", {})

        # Boss activity check (index <= 6 means "2月内活跃" or worse)
        active_desc = boss_info.get("activeTimeDesc", "")
        active_levels = self._config.get("active_levels", [])
        if active_desc and active_desc in active_levels:
            level_index = active_levels.index(active_desc)
            if level_index <= 6:  # 本月活跃 = 7, anything below is too inactive
                return f"BOSS不活跃: {active_desc}"

        # Job status check
        invalid = job_info.get("invalidStatus")
        if invalid and invalid != 0:
            return "职位已下线"

        # Company blacklist
        company_name = brand_info.get("brandName", "").lower()
        if company_name and self._company_blacklist:
            for bl in self._company_blacklist:
                if bl in company_name:
                    return f"公司黑名单: {bl}"

        # Keyword blacklist (check job name + JD text)
        if self._keyword_blacklist:
            job_name = job_info.get("jobName", "").lower()
            post_desc = job_info.get("postDescription", "").lower()
            check_text = f"{job_name} {post_desc}"
            for bl in self._keyword_blacklist:
                if bl in check_text:
                    return f"关键词黑名单: {bl}"

        return None

    def _random_delay(self, min_sec: float, max_sec: float) -> None:
        """Interruptible random delay. Respects both stop and pause."""
        total = random.uniform(min_sec, max_sec)
        elapsed = 0.0
        while elapsed < total and not self._stopped:
            self._wait_if_paused()
            if self._stopped:
                break
            step = min(0.5, total - elapsed)
            time.sleep(step)
            elapsed += step

    def _operation_delay(self) -> None:
        """Standard delay between operations (5-15 seconds), interruptible."""
        self._random_delay(5, 15)
