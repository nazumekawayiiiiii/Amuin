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


_DISCLAIMER = """\
Amuin 使用必读及免责声明
请您务必逐条仔细阅读。若您不接受以下提到的任何内容，请立即删除并停止使用本程序。

1. 项目定位与稳定性（重要提示）
Amuin 的初衷纯粹是为了个人场景下的技术学习与代码交流。这是一个由个人业余时间开发的，\
目前处于极其早期的测试阶段（Alpha）。本程序没有经过大量、严谨的跨环境边界测试与迭代，\
其测试用例仅覆盖了原作者个人的特定使用场景。
因此，在您的设备上运行时，极大概率会出现报错、UI卡死、甚至程序闪退的情况。\
如果您遇到这些问题，欢迎在 GitHub 提交 Issue 探讨。

2. 违约风险与"风险隔离"（务必重视）
本程序本质上属于一款模拟用户行为的自动化辅助工具。其运行机制不可避免地与\
《BOSS直聘用户协议》的相关条款（如针对自动化脚本、非正常高频访问的限制）相违背。
一旦您的自动化行为被平台风控系统监测到，您的账号将面临包括但不限于：\
被强制下线、限制聊天功能、甚至账号被永久封禁等严厉处罚。
强烈建议：为了进行严格的风险隔离，请务必使用小号或专门注册的全新账号来运行本程序！\
不推荐使用您的主力大号进行测试。您选择使用本程序，即代表您完全知晓并愿意自行承担\
账号被封禁等一切不利后果，本程序及原作者对此概不负责。

3. 隐私保护与木马防范
Amuin 需要在本地存储您的登录凭据（Cookie）以接管网页操作。请放心，本程序仅会将数据\
存储在您的本地计算机中，且仅在自动操作时将凭据提交给目标招聘平台。程序绝对不会收集\
您的任何个人信息，也不会将其上传、泄露给任何第三方（如猎头公司、数据机构）。
安全警告：由于本程序完全开源，任何人都可以修改源码（例如植入窃取 Cookie 的后门木马）\
并重新打包发布。因此，请务必认清来源和版本。

4. 免费与非盈利声明
Amuin 是一款完全免费的开源工具，没有任何内置的付费解锁功能，原作者也从未试图通过\
本程序牟取任何利益。如果您是通过淘宝、闲鱼等渠道付费"购买"的本程序，或者在使用时\
被提示"需要扫码付费/加群付费"，那么您百分之百遇到了诈骗，或者下载到了被恶意二次\
篡改的版本。请直接向卖家发起退款维权，原作者不提供任何与金钱交易相关的售后服务。

5. 求职结果免责与网页改版
本程序仅负责代替您执行机械的"点击"与"打招呼"动作。对您投递的岗位真实性、公司的\
可靠性，以及您最终的面试与 Offer 结果，本程序不提供任何担保。请您在沟通过程中自行\
擦亮眼睛，防范招聘诈骗。
此外，目标网站的 UI 改版或 A/B 测试随时可能导致 Amuin 的核心脚本瞬间失效。当您发现\
程序无限循环或疯狂报错时，通常意味着网站结构变了，请停止使用并等待代码更新。
"""


class ClawApp(ctk.CTk):
    """Main application window."""

    def __init__(self, queue_handler: QueueLogHandler):
        super().__init__()

        self._queue_handler = queue_handler
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._running = False
        self._disclaimer_accepted = False

        # Callbacks set by main.py
        self.on_start: callable = None  # (stop_event, pause_event) -> None
        self.on_logout: callable = None  # () -> None
        self.on_followup_send: callable = None  # (job_encrypt_id, template_type) -> bool
        self.on_followup_batch: callable = None  # (items) -> int

        self._setup_window()
        self._build_ui()

        # Disclaimer gate — must accept before using the app
        if not self._show_disclaimer():
            return
        self._disclaimer_accepted = True
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

    def _show_disclaimer(self) -> bool:
        """Show disclaimer dialog. Returns True only if user clicks agree."""
        agreed = [False]

        dialog = ctk.CTkToplevel(self)
        dialog.title("Amuin 使用必读及免责声明")
        dialog.geometry("660x520")
        dialog.resizable(False, False)

        # Center on screen
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 660) // 2
        y = (dialog.winfo_screenheight() - 520) // 2
        dialog.geometry(f"+{x}+{y}")

        icon_path = STATIC_DIR / "niuma.ico"
        if icon_path.is_file():
            dialog.after(250, lambda: dialog.iconbitmap(str(icon_path)))

        # Disclaimer text (read-only)
        text_box = ctk.CTkTextbox(
            dialog, wrap="word", font=ctk.CTkFont(size=13),
        )
        text_box.pack(fill="both", expand=True, padx=15, pady=(15, 10))
        text_box.insert("end", _DISCLAIMER)
        text_box.configure(state="disabled")

        # Buttons
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=(0, 15))

        def on_agree():
            agreed[0] = True
            dialog.destroy()

        def on_disagree():
            dialog.destroy()

        ctk.CTkButton(
            btn_frame, text="不同意", width=100, command=on_disagree,
            fg_color="#666666", hover_color="#555555",
        ).pack(side="right", padx=5)
        ctk.CTkButton(
            btn_frame, text="同意并继续", width=120, command=on_agree,
        ).pack(side="right", padx=5)

        dialog.protocol("WM_DELETE_WINDOW", on_disagree)
        dialog.grab_set()
        self.wait_window(dialog)

        return agreed[0]

    def mainloop(self, *args, **kwargs):
        if not self._disclaimer_accepted:
            self.destroy()
            return
        super().mainloop(*args, **kwargs)

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
