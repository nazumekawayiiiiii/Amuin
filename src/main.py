"""Amuin - Application entry point.

Usage:
    python -m src        (from project root)
    python run.py        (from project root)

Architecture:
    - Main thread: customtkinter UI (tkinter requirement)
    - Background thread: browser automation (Playwright)
    - Communication: logging queue + command queue for follow-up sends
"""

import json
import sys
import time
import random
import logging
import threading
import queue

from .browser.manager import BrowserManager
from .browser.auth import AuthManager
from .maintenance.validator import ToolValidator
from .agents.llm_client import LLMClient
from .agents.evaluator import Evaluator
from .db.database import Database
from .db.models import ConversationStore
from .core.dedup import DedupManager
from .core.combination import CombinationGenerator
from .core.search_loop import SearchLoop
from .core.message_monitor import MessageMonitor
from .utils.logger import setup_logging
from .utils.notifier import notify_new_message
from .utils.paths import DATA_DIR, CONFIG_DIR
from .ui.app import ClawApp


class ClawEngine:
    """Automation engine — runs in background thread.

    Manages browser, search loops, message monitoring,
    and processes follow-up send commands from the UI.
    """

    def __init__(self, app: ClawApp):
        self._app = app
        self._log = logging.getLogger("claw")
        self._db: Database | None = None
        self._store: ConversationStore | None = None
        self._browser_mgr: BrowserManager | None = None
        self._monitor: MessageMonitor | None = None
        self._followup_queue: queue.Queue = queue.Queue()

    def run(
        self,
        knowledge: dict,
        stop_event: threading.Event,
        pause_event: threading.Event,
        enabled_combos: list[str],
    ) -> None:
        """Main automation entry point (called from background thread)."""
        log = self._log

        llm_cfg = knowledge.get("llm", {})
        static = knowledge.get("static", {})
        traversal = knowledge.get("traversal", {})
        limits = static.get("limits", {})

        # 1. Ensure data directory + Database
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log.info("[INIT] 连接数据库...")
        self._db = Database(DATA_DIR / "claw.db")
        self._db.connect()
        self._db.cleanup_expired()
        self._store = ConversationStore(self._db)

        try:
            # 2. Launch browser
            self._browser_mgr = BrowserManager(data_dir=DATA_DIR)
            log.info("[INIT] 启动浏览器...")
            page = self._browser_mgr.start()
            log.info("[INIT] 浏览器已启动")

            # 3. Login (ensure_logged_in handles check + QR + state save)
            auth_mgr = AuthManager(
                page=page, data_dir=DATA_DIR,
                browser_mgr=self._browser_mgr,
                stop_event=stop_event,
            )

            if not auth_mgr.ensure_logged_in():
                if not stop_event.is_set():
                    log.error("[INIT] 登录失败或超时，请重试")
                return

            # Update page reference (may have changed after context restart)
            page = self._browser_mgr.page

            if stop_event.is_set():
                return

            # 4. Tool validation
            log.info("[INIT] 验证工具选择器...")
            validator = ToolValidator(page)
            report = validator.validate(run_level3=True)

            if not report.passed:
                log.error("[INIT] 关键工具验证失败，无法继续")
                for f in report.failures:
                    log.error(f"  ✗ {f.name}: {f.message}")
                return

            if report.warnings:
                log.warning(
                    f"[INIT] {len(report.warnings)} 个非关键验证警告，继续运行"
                )

            if stop_event.is_set():
                return

            # 5. Dedup
            dedup = DedupManager(self._store)
            dedup.load()

            # 6. LLM client + Evaluator
            llm_client = LLMClient(
                base_url=llm_cfg["base_url"],
                api_key=llm_cfg["api_key"],
                model=llm_cfg.get("model", ""),
            )
            evaluator = Evaluator(llm_client, static)

            # 7. Combinations (from UI checkboxes)
            combo_gen = CombinationGenerator(traversal)
            combo_gen.generate()
            all_combos = combo_gen.get_enabled()

            if not all_combos:
                log.error("[INIT] 无可用搜索组合（请检查知识库 traversal 配置）")
                return

            log.info(f"[WORK] 启用 {len(all_combos)} 个组合")

            # 8. DB persistence callbacks
            def on_match(detail: dict, result) -> None:
                self._store.insert(
                    detail,
                    result.to_dict() if result else None,
                    result.greeting if result else "",
                )

            def on_skip(detail: dict, result) -> None:
                self._store.insert(
                    detail, result.to_dict() if result else None
                )

            # 9. Run search loops
            daily_limit = limits.get("daily_apply_max", 40)
            daily_count = self._store.count_today_applied()
            total_browsed = 0
            total_applied = 0

            self._app.update_daily_count(daily_count, daily_limit)

            if daily_count > 0:
                log.info(f"[WORK] 今日已投递 {daily_count} 个（从数据库恢复）")

            rest_range = limits.get("rest_duration_sec", [120, 300])

            for i, combo in enumerate(all_combos, 1):
                if stop_event.is_set():
                    break
                if daily_count >= daily_limit:
                    log.info(
                        f"[WORK] 今日投递达上限 ({daily_count}/{daily_limit})，停止"
                    )
                    break

                # Pause support (with browser liveness check)
                while pause_event.is_set() and not stop_event.is_set():
                    time.sleep(1)
                    try:
                        page.evaluate("1")
                    except Exception:
                        log.warning("[WORK] 暂停期间浏览器已关闭，自动停止")
                        stop_event.set()
                        break

                log.info(
                    f"[WORK] 组合 {i}/{len(all_combos)}: {combo.label}"
                )

                preferences = static.get("preferences", {})

                loop = SearchLoop(
                    page=page,
                    evaluator=evaluator,
                    known_job_ids=dedup._seen,
                    on_match=on_match,
                    on_skip=on_skip,
                    daily_limit=daily_limit,
                    daily_count=daily_count,
                    stop_event=stop_event,
                    pause_event=pause_event,
                    preferences=preferences,
                )

                stats = loop.run(
                    keyword=combo.keyword, filters=combo.filters
                )

                daily_count += stats.applied
                total_browsed += stats.browsed
                total_applied += stats.applied

                self._app.update_daily_count(daily_count, daily_limit)

                # Rest between combinations
                if (
                    i < len(all_combos)
                    and daily_count < daily_limit
                    and not stop_event.is_set()
                ):
                    rest = random.uniform(rest_range[0], rest_range[1])
                    log.info(f"[WORK] 组合间休息 {rest:.0f} 秒...")
                    self._interruptible_sleep(rest, stop_event, pause_event)

            log.info(
                f"[WORK] 本轮完成: 浏览 {total_browsed}, "
                f"投递 {total_applied}, 今日总计 {daily_count}"
            )

            # 10. Refresh follow-up tab data
            self._refresh_followup_data()

            # 11. Message monitoring + follow-up command processing
            if not stop_event.is_set():
                self._run_monitor_loop(
                    page, stop_event, pause_event, daily_limit
                )

        except KeyboardInterrupt:
            log.info("用户中断")
        except Exception as e:
            log.error(f"[ERROR] {e}", exc_info=True)
        finally:
            if self._browser_mgr:
                self._browser_mgr.stop()
            if self._db:
                self._db.close()
            log.info("[INIT] 已关闭")

    def _run_monitor_loop(
        self,
        page,
        stop_event: threading.Event,
        pause_event: threading.Event,
        daily_limit: int,
    ) -> None:
        """Message monitoring + follow-up command processing loop."""
        log = self._log

        def wait_if_paused() -> None:
            """Block while paused; auto-stop if browser dies."""
            ticks = 0
            while pause_event.is_set() and not stop_event.is_set():
                time.sleep(0.5)
                ticks += 1
                if ticks % 10 == 0:
                    try:
                        page.evaluate("1")
                    except Exception:
                        log.warning("[MSG] 暂停期间浏览器已关闭，自动停止")
                        stop_event.set()
                        break

        def on_new_message(msg: dict) -> None:
            notify_new_message(msg)

        self._monitor = MessageMonitor(
            page, self._store, on_new_message=on_new_message
        )

        log.info("[MSG] 投递完成，进入消息监控模式")
        interval_minutes = 30

        while not stop_event.is_set():
            # Block while paused
            wait_if_paused()
            if stop_event.is_set():
                break

            # Process any pending follow-up commands
            self._process_followup_commands(page)

            # Check for new messages
            try:
                self._monitor.check_once()
                self._refresh_followup_data()
                self._app.update_daily_count(
                    self._store.count_today_applied(), daily_limit
                )
            except Exception as e:
                log.error(f"[MSG] 检查失败: {e}")

            # Wait for next check (interruptible by stop and pause)
            for _ in range(interval_minutes * 60):
                if stop_event.is_set():
                    break
                # Block while paused (don't count paused time toward interval)
                wait_if_paused()
                if stop_event.is_set():
                    break
                # Check follow-up queue every second
                self._process_followup_commands(page)
                time.sleep(1)

        log.info("[MSG] 消息监控已停止")

    def _process_followup_commands(self, page) -> None:
        """Process pending follow-up send commands from the UI."""
        from .tools.reply_tool import ReplyTool
        from .tools.base_tool import load_tool_config

        while not self._followup_queue.empty():
            try:
                cmd = self._followup_queue.get_nowait()
            except queue.Empty:
                break

            job_id = cmd.get("job_encrypt_id", "")
            template_type = cmd.get("template_type", "")

            if not job_id or not template_type:
                continue

            # Get template text from DB
            template_text = self._store.get_reply_template(
                job_id, template_type
            )
            if not template_text:
                self._log.warning(
                    f"[WORK] 无跟进模板: {job_id[:10]}... ({template_type})"
                )
                continue

            # Send via ReplyTool
            config = load_tool_config()
            reply_tool = ReplyTool(
                page, config.get("reply_tool", {}), config.get("common", {})
            )
            success = reply_tool.send_message(job_id, template_text)

            if success:
                self._store.increment_followup(job_id)
                self._log.info(
                    f"[WORK] 跟进消息已发送: {job_id[:10]}..."
                )
            else:
                self._log.warning(
                    f"[WORK] 跟进消息发送失败: {job_id[:10]}..."
                )

    def _refresh_followup_data(self) -> None:
        """Push updated follow-up data to the UI."""
        if not self._store:
            return
        followups = self._store.list_for_followup()
        replies = self._store.list_with_replies()
        self._app.update_followup_data(followups, replies)

    def queue_followup(
        self, job_encrypt_id: str, template_type: str
    ) -> bool:
        """Queue a follow-up send command (called from UI thread)."""
        self._followup_queue.put({
            "job_encrypt_id": job_encrypt_id,
            "template_type": template_type,
        })
        return True

    def queue_batch_followup(self, items: list[dict]) -> int:
        """Queue multiple follow-up commands."""
        for item in items:
            self._followup_queue.put(item)
        return len(items)

    def logout(self) -> None:
        """Clear login state. Safe to call when automation is not running."""
        log = self._log
        if self._browser_mgr:
            self._browser_mgr.logout()
        else:
            mgr = BrowserManager(DATA_DIR)
            mgr.logout()
        log.info("[INIT] 登出完成，下次启动需要重新登录")

    @staticmethod
    def _interruptible_sleep(
        seconds: float,
        stop_event: threading.Event,
        pause_event: threading.Event | None = None,
    ) -> None:
        """Sleep that can be interrupted by stop_event and paused by pause_event."""
        for _ in range(int(seconds)):
            if stop_event.is_set():
                return
            # Block while paused
            if pause_event is not None:
                while pause_event.is_set() and not stop_event.is_set():
                    time.sleep(0.5)
                if stop_event.is_set():
                    return
            time.sleep(1)
        remainder = seconds - int(seconds)
        if remainder > 0 and not stop_event.is_set():
            time.sleep(remainder)


def main() -> int:
    # Setup logging with queue handler for UI
    queue_handler = setup_logging()
    log = logging.getLogger("claw")
    log.info("[INIT] Amuin 启动中...")

    # Create UI
    app = ClawApp(queue_handler)

    # Create engine
    engine = ClawEngine(app)

    # Wire callbacks
    app.on_start = engine.run
    app.on_logout = engine.logout
    app.on_followup_send = engine.queue_followup
    app.on_followup_batch = engine.queue_batch_followup

    # Run UI (blocks until window is closed)
    app.mainloop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
