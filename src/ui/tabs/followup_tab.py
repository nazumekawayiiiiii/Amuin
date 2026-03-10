"""Follow-up tab — one-click send pre-generated templates.

Shows two sections:
  1. 已读不回 (read but no reply) — send read_no_reply template
  2. 直接不读 (not read) — send not_read template

Each row has: score, company, position, days since, [send] button.
Bottom: batch send button.
"""

import logging
from datetime import datetime

import customtkinter as ctk

log = logging.getLogger("claw")


class FollowupTab(ctk.CTkFrame):
    """Follow-up panel for sending pre-generated reply templates."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")

        # Callbacks set by app.py
        self.on_send: callable = None  # (job_encrypt_id, template_type) -> bool
        self.on_batch_send: callable = None  # (items) -> int

        self._followup_items: list[dict] = []
        self._reply_items: list[dict] = []

        self._build_ui()

    def _build_ui(self) -> None:
        # Top: refresh + batch send
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=5, pady=5)

        self._batch_btn = ctk.CTkButton(
            top, text="一键群发前10个", width=150,
            command=self._on_batch_send,
        )
        self._batch_btn.pack(side="right", padx=5)

        self._summary_label = ctk.CTkLabel(
            top, text="待跟进: 0 | HR已回复: 0", anchor="w"
        )
        self._summary_label.pack(side="left", padx=5)

        # Scrollable content
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=5, pady=5)

        self._placeholder = ctk.CTkLabel(
            self._scroll,
            text="暂无跟进数据，开始投递后此处会显示可跟进岗位",
            text_color="gray",
        )
        self._placeholder.pack(pady=20)

    def load_data(
        self, followups: list[dict], replies: list[dict]
    ) -> None:
        """Refresh the follow-up list with data from DB.

        Args:
            followups: ConversationStore.list_for_followup() results.
            replies: ConversationStore.list_with_replies() results.
        """
        self._followup_items = followups
        self._reply_items = replies
        self._render()

    def _render(self) -> None:
        # Clear
        for w in self._scroll.winfo_children():
            w.destroy()

        total_followup = len(self._followup_items)
        total_reply = len(self._reply_items)

        self._summary_label.configure(
            text=f"待跟进: {total_followup} | HR已回复: {total_reply}"
        )

        if not self._followup_items and not self._reply_items:
            ctk.CTkLabel(
                self._scroll,
                text="暂无跟进数据",
                text_color="gray",
            ).pack(pady=20)
            return

        # ── HR 已回复 section ──
        if self._reply_items:
            self._add_section_header("HR 已回复")
            for item in self._reply_items:
                self._add_reply_row(item)

        # ── 待跟进 section ──
        if self._followup_items:
            self._add_section_header("待跟进 (按评分排序)")
            for item in self._followup_items:
                self._add_followup_row(item)

    def _add_section_header(self, title: str) -> None:
        ctk.CTkLabel(
            self._scroll, text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=5, pady=(10, 5))

    def _add_reply_row(self, item: dict) -> None:
        row = ctk.CTkFrame(self._scroll)
        row.pack(fill="x", padx=5, pady=2)

        score = item.get("score", 0)
        company = item.get("company", "")
        position = item.get("position", "")

        info_text = f"{score}分  {company} - {position}"
        ctk.CTkLabel(
            row, text=info_text, anchor="w",
            text_color="#4FC3F7",
        ).pack(side="left", padx=5, fill="x", expand=True)

        ctk.CTkLabel(
            row, text="HR已回复", text_color="#66BB6A",
        ).pack(side="right", padx=10)

    def _add_followup_row(self, item: dict) -> None:
        row = ctk.CTkFrame(self._scroll)
        row.pack(fill="x", padx=5, pady=2)

        score = item.get("score", 0)
        company = item.get("company", "")
        position = item.get("position", "")
        last_msg_time = item.get("last_msg_time", "")
        followup_count = item.get("followup_count", 0)

        # Calculate days since last message
        days_text = ""
        if last_msg_time:
            try:
                last_dt = datetime.fromisoformat(last_msg_time)
                days = (datetime.now() - last_dt).days
                days_text = f"{days}天前"
            except ValueError:
                days_text = ""

        status_text = "已读不回" if followup_count == 0 else f"已跟进{followup_count}次"
        info_text = f"{score}分  {company} - {position}  {days_text}  {status_text}"

        ctk.CTkLabel(
            row, text=info_text, anchor="w",
        ).pack(side="left", padx=5, fill="x", expand=True)

        job_id = item.get("job_encrypt_id", "")
        template_type = "read_no_reply" if followup_count == 0 else "not_read"

        ctk.CTkButton(
            row, text="发送跟进", width=80,
            command=lambda jid=job_id, tt=template_type: self._on_send(jid, tt),
        ).pack(side="right", padx=5)

    def _on_send(self, job_encrypt_id: str, template_type: str) -> None:
        if self.on_send:
            success = self.on_send(job_encrypt_id, template_type)
            if success:
                log.info(f"[WORK] 跟进消息已发送: {job_encrypt_id[:10]}...")
            else:
                log.warning(f"[WORK] 跟进消息发送失败: {job_encrypt_id[:10]}...")

    def _on_batch_send(self) -> None:
        if not self._followup_items:
            return

        batch = self._followup_items[:10]
        items = []
        for item in batch:
            fc = item.get("followup_count", 0)
            items.append({
                "job_encrypt_id": item.get("job_encrypt_id", ""),
                "template_type": "read_no_reply" if fc == 0 else "not_read",
            })

        if self.on_batch_send:
            count = self.on_batch_send(items)
            log.info(f"[WORK] 批量跟进完成: {count}/{len(items)} 条")
