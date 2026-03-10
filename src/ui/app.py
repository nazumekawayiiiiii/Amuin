"""Amuin main window — customtkinter application.

Hosts 4 tabs + status bar + control buttons.
Automation runs in a background thread; UI runs in the main thread.
"""

import json
import threading

import customtkinter as ctk

from .tabs.knowledge_tab import KnowledgeTab
from .tabs.combination_tab import CombinationTab
from .tabs.followup_tab import FollowupTab
from .tabs.log_tab import LogTab
from ..utils.logger import QueueLogHandler
from ..utils.paths import CONFIG_DIR, STATIC_DIR


class ClawApp(ctk.CTk):
    """Main application window."""

    def __init__(self, queue_handler: QueueLogHandler):
        super().__init__()

        self._queue_handler = queue_handler
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._running = False

        # Callbacks set by main.py
        self.on_start: callable = None  # (stop_event, pause_event) -> None
        self.on_logout: callable = None  # () -> None
        self.on_followup_send: callable = None  # (job_encrypt_id, template_type) -> bool
        self.on_followup_batch: callable = None  # (items) -> int

        self._setup_window()
        self._build_ui()
        self._poll_logs()

    def _setup_window(self) -> None:
        self.title("Amuin - AI 求职助手")
        self.geometry("900x650")
        self.minsize(750, 500)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Window icon
        icon_path = STATIC_DIR / "niuma.ico"
        if icon_path.is_file():
            self.iconbitmap(str(icon_path))

    def _build_ui(self) -> None:
        # ── Tab View ──
        self._tabview = ctk.CTkTabview(self, anchor="nw")
        self._tabview.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self._tabview.add("配置信息")
        self._tabview.add("组合管理")
        self._tabview.add("跟进")
        self._tabview.add("日志")

        # Tab contents
        self._knowledge_tab = KnowledgeTab(
            self._tabview.tab("配置信息"), CONFIG_DIR / "knowledge.json"
        )
        self._knowledge_tab.pack(fill="both", expand=True)

        self._combination_tab = CombinationTab(self._tabview.tab("组合管理"))
        self._combination_tab.pack(fill="both", expand=True)

        self._followup_tab = FollowupTab(self._tabview.tab("跟进"))
        self._followup_tab.pack(fill="both", expand=True)
        self._followup_tab.on_send = self._handle_followup_send
        self._followup_tab.on_batch_send = self._handle_followup_batch

        self._log_tab = LogTab(self._tabview.tab("日志"))
        self._log_tab.pack(fill="both", expand=True)

        # Wire knowledge save → combination preview refresh
        self._knowledge_tab.on_save = self._on_knowledge_saved

        # Wire tab change → refresh combination preview on switch
        self._tabview.configure(command=self._on_tab_changed)

        # ── Notification bar (above bottom controls) ──
        notif_frame = ctk.CTkFrame(self, fg_color="transparent", height=24)
        notif_frame.pack(fill="x", padx=15, pady=(5, 0))
        notif_frame.pack_propagate(False)

        self._notification_label = ctk.CTkLabel(
            notif_frame,
            text="",
            anchor="w",
            text_color="#FFD700",
            font=ctk.CTkFont(size=12),
        )
        self._notification_label.pack(side="left", fill="x", expand=True)

        # ── Bottom Frame: status bar + controls ──
        bottom = ctk.CTkFrame(self)
        bottom.pack(fill="x", padx=10, pady=(5, 10))

        # Status indicators
        status_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        status_frame.pack(side="left", fill="x", expand=True)

        self._status_label = ctk.CTkLabel(
            status_frame,
            text="就绪",
            anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self._status_label.pack(side="left", padx=(5, 20))

        self._daily_label = ctk.CTkLabel(
            status_frame,
            text="今日: 0/40",
            anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self._daily_label.pack(side="left", padx=(0, 20))

        # Control buttons
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(side="right")

        self._start_btn = ctk.CTkButton(
            btn_frame, text="开始", width=80, command=self._on_start
        )
        self._start_btn.pack(side="left", padx=5)

        self._pause_btn = ctk.CTkButton(
            btn_frame, text="暂停", width=80,
            command=self._on_pause, state="disabled"
        )
        self._pause_btn.pack(side="left", padx=5)

        self._stop_btn = ctk.CTkButton(
            btn_frame, text="停止", width=80,
            command=self._on_stop, state="disabled",
            fg_color="#CC3333", hover_color="#AA2222",
        )
        self._stop_btn.pack(side="left", padx=5)

        self._logout_btn = ctk.CTkButton(
            btn_frame, text="登出", width=80,
            command=self._on_logout,
            fg_color="#666666", hover_color="#555555",
        )
        self._logout_btn.pack(side="left", padx=(15, 5))

    # ── Knowledge → Combination sync ──

    def _on_knowledge_saved(self) -> None:
        """Refresh combination tab when knowledge is saved."""
        knowledge = self._knowledge_tab.get_knowledge()
        traversal = knowledge.get("traversal", {})
        self._combination_tab.load_traversal(traversal)

    def _on_tab_changed(self) -> None:
        """Refresh combination preview when switching to that tab."""
        current = self._tabview.get()
        if current == "组合管理" and not self._running:
            knowledge = self._knowledge_tab.get_knowledge()
            traversal = knowledge.get("traversal", {})
            if any(traversal.get(k) for k in traversal):
                self._combination_tab.load_traversal(traversal)

    # ── Log polling ──

    def _poll_logs(self) -> None:
        """Poll the log queue and push entries to the log tab."""
        records = self._queue_handler.get_pending()
        for record in records:
            formatted = self._queue_handler.format(record)
            self._log_tab.append(formatted, record.levelno)

            # Update notification label for [MSG] entries
            if "[MSG]" in record.getMessage() and "回复" in record.getMessage():
                self._notification_label.configure(
                    text=f"🔔 {record.getMessage()[:60]}"
                )

        self.after(200, self._poll_logs)

    # ── Control handlers ──

    def _on_start(self) -> None:
        if self._running:
            return

        # Reload and validate knowledge
        knowledge = self._knowledge_tab.get_knowledge()
        if not knowledge:
            self._set_status("请先配置信息")
            return

        llm_cfg = knowledge.get("llm", {})
        if not llm_cfg.get("base_url") or not llm_cfg.get("api_key"):
            self._set_status("请先配置 LLM (base_url, api_key)")
            return

        # Save knowledge before starting
        self._knowledge_tab.save()

        # Refresh combination tab
        traversal = knowledge.get("traversal", {})
        self._combination_tab.load_traversal(traversal)

        self._running = True
        self._stop_event.clear()
        self._pause_event.clear()

        self._start_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal")
        self._stop_btn.configure(state="normal")
        self._set_status("运行中...")

        # Launch automation in background thread
        self._worker_thread = threading.Thread(
            target=self._run_worker,
            args=(knowledge,),
            daemon=True,
        )
        self._worker_thread.start()

    def _run_worker(self, knowledge: dict) -> None:
        """Background thread — runs the automation pipeline."""
        try:
            enabled_combos = self._combination_tab.get_enabled_labels()
            if self.on_start:
                self.on_start(
                    knowledge=knowledge,
                    stop_event=self._stop_event,
                    pause_event=self._pause_event,
                    enabled_combos=enabled_combos,
                )
        except Exception as e:
            log.error(f"[ERROR] 工作线程异常: {e}", exc_info=True)
        finally:
            self.after(0, self._on_worker_done)

    def _on_worker_done(self) -> None:
        """Called when the background worker finishes."""
        self._running = False
        self._start_btn.configure(state="normal")
        self._pause_btn.configure(state="disabled")
        self._stop_btn.configure(state="disabled")
        self._set_status("已停止")

    def _on_pause(self) -> None:
        if self._pause_event.is_set():
            self._pause_event.clear()
            self._pause_btn.configure(text="暂停")
            self._set_status("运行中...")
        else:
            self._pause_event.set()
            self._pause_btn.configure(text="继续")
            self._set_status("已暂停")

    def _on_stop(self) -> None:
        self._stop_event.set()
        self._set_status("正在停止...")
        self._stop_btn.configure(state="disabled")

    def _on_logout(self) -> None:
        """Clear login state. Stops automation first if running."""
        if self._running:
            self._on_stop()
        if self.on_logout:
            self.on_logout()
        self._set_status("已登出，下次启动需重新扫码")

    # ── Follow-up handlers ──

    def _handle_followup_send(self, job_encrypt_id: str, template_type: str) -> bool:
        if self.on_followup_send:
            return self.on_followup_send(job_encrypt_id, template_type)
        return False

    def _handle_followup_batch(self, items: list[dict]) -> int:
        if self.on_followup_batch:
            return self.on_followup_batch(items)
        return 0

    # ── Public methods for main.py ──

    def update_daily_count(self, count: int, limit: int) -> None:
        """Thread-safe daily count update."""
        self.after(0, lambda: self._daily_label.configure(
            text=f"今日: {count}/{limit}"
        ))

    def update_followup_data(self, followups: list[dict], replies: list[dict]) -> None:
        """Refresh follow-up tab data. Call from any thread."""
        self.after(0, lambda: self._followup_tab.load_data(followups, replies))

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    @property
    def combination_tab(self) -> CombinationTab:
        return self._combination_tab

    @property
    def followup_tab(self) -> FollowupTab:
        return self._followup_tab
