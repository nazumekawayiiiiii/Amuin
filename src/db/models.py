"""Conversation data access layer.

CRUD operations for the conversations table.
All "memory" in Claw lives here — no LLM context history needed.
"""

import json
import logging
from datetime import datetime

from .database import Database

log = logging.getLogger("claw")


class ConversationStore:
    """Data access for conversations table.

    Usage:
        store = ConversationStore(db)
        store.insert(job_detail, eval_result, greeting)
        jobs = store.get_known_job_ids()
        followups = store.list_for_followup()
    """

    def __init__(self, db: Database):
        self._db = db

    # ── Insert / Update ──

    def insert(
        self,
        job_detail: dict,
        eval_result: dict | None = None,
        greeting_used: str = "",
    ) -> int | None:
        """Insert a new conversation record.

        Args:
            job_detail: Full job detail from JobDetailTool (__vue__.data).
            eval_result: EvaluationResult.to_dict(), or None for skipped jobs.
            greeting_used: The actual greeting message sent.

        Returns:
            Row id, or None if duplicate.
        """
        job_info = job_detail.get("jobInfo", {})
        boss_info = job_detail.get("bossInfo", {})
        brand_info = job_detail.get("brandComInfo", {})

        job_id = job_info.get("encryptId", "")
        if not job_id:
            return None

        now = datetime.now().isoformat()

        reply_templates = ""
        score = 0
        score_reason = ""
        decision = "skip"
        if eval_result:
            score = eval_result.get("score", 0)
            score_reason = eval_result.get("reason", "")
            decision = eval_result.get("decision", "skip")
            templates = eval_result.get("reply_templates", {})
            if templates:
                reply_templates = json.dumps(templates, ensure_ascii=False)

        try:
            cursor = self._db.conn.execute(
                """INSERT INTO conversations
                   (job_encrypt_id, boss_encrypt_id, company, position,
                    salary_range, city, status, score, score_reason,
                    decision, greeting_used, reply_templates,
                    last_msg_from, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    boss_info.get("encryptBossId", boss_info.get("name", "")),
                    brand_info.get("brandName", ""),
                    job_info.get("jobName", ""),
                    job_info.get("salaryDesc", ""),
                    job_info.get("locationName", ""),
                    "active" if decision == "match" else "skipped",
                    score,
                    score_reason,
                    decision,
                    greeting_used,
                    reply_templates,
                    "self" if decision == "match" else "",
                    now,
                    now,
                ),
            )
            self._db.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                return None  # Duplicate, already exists
            log.error(f"[ERROR] 数据库写入失败: {e}")
            return None

    def update_message_status(
        self, job_encrypt_id: str, last_msg_from: str
    ) -> None:
        """Update last message info for a conversation."""
        now = datetime.now().isoformat()
        self._db.conn.execute(
            """UPDATE conversations
               SET last_msg_time = ?, last_msg_from = ?, updated_at = ?
               WHERE job_encrypt_id = ?""",
            (now, last_msg_from, now, job_encrypt_id),
        )
        self._db.conn.commit()

    def increment_followup(self, job_encrypt_id: str) -> None:
        """Increment followup count after sending a follow-up message."""
        now = datetime.now().isoformat()
        self._db.conn.execute(
            """UPDATE conversations
               SET followup_count = followup_count + 1,
                   last_msg_time = ?, last_msg_from = 'self', updated_at = ?
               WHERE job_encrypt_id = ?""",
            (now, now, job_encrypt_id),
        )
        self._db.conn.commit()

    def update_status(self, job_encrypt_id: str, status: str) -> None:
        """Update conversation status (active / waiting / archived / skipped)."""
        now = datetime.now().isoformat()
        self._db.conn.execute(
            """UPDATE conversations SET status = ?, updated_at = ?
               WHERE job_encrypt_id = ?""",
            (status, now, job_encrypt_id),
        )
        self._db.conn.commit()

    # ── Queries ──

    def get_known_job_ids(self) -> set[str]:
        """Get all job_encrypt_ids in the database (for dedup)."""
        rows = self._db.conn.execute(
            "SELECT job_encrypt_id FROM conversations"
        ).fetchall()
        return {row["job_encrypt_id"] for row in rows}

    def exists(self, job_encrypt_id: str) -> bool:
        """Check if a job is already in the database."""
        row = self._db.conn.execute(
            "SELECT 1 FROM conversations WHERE job_encrypt_id = ?",
            (job_encrypt_id,),
        ).fetchone()
        return row is not None

    def get_by_job_id(self, job_encrypt_id: str) -> dict | None:
        """Get a conversation record by job_encrypt_id."""
        row = self._db.conn.execute(
            "SELECT * FROM conversations WHERE job_encrypt_id = ?",
            (job_encrypt_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_for_followup(
        self, min_score: int = 0, limit: int = 20
    ) -> list[dict]:
        """List conversations suitable for follow-up.

        Returns jobs where:
          - status = 'active'
          - decision = 'match'
          - last message was from self (HR hasn't replied)
          - ordered by score descending
        """
        rows = self._db.conn.execute(
            """SELECT * FROM conversations
               WHERE status = 'active'
                 AND decision = 'match'
                 AND last_msg_from = 'self'
                 AND score >= ?
               ORDER BY score DESC
               LIMIT ?""",
            (min_score, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_with_replies(self, limit: int = 20) -> list[dict]:
        """List conversations where HR has replied (for notification)."""
        rows = self._db.conn.execute(
            """SELECT * FROM conversations
               WHERE status = 'active'
                 AND last_msg_from = 'boss'
               ORDER BY last_msg_time DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_reply_template(
        self, job_encrypt_id: str, template_type: str
    ) -> str | None:
        """Get a specific pre-generated reply template.

        Args:
            job_encrypt_id: Job ID.
            template_type: One of "read_no_reply", "not_read",
                           "ask_resume", "interview_follow".

        Returns:
            Template text, or None.
        """
        row = self._db.conn.execute(
            "SELECT reply_templates FROM conversations WHERE job_encrypt_id = ?",
            (job_encrypt_id,),
        ).fetchone()
        if not row or not row["reply_templates"]:
            return None
        try:
            templates = json.loads(row["reply_templates"])
            return templates.get(template_type)
        except (json.JSONDecodeError, AttributeError):
            return None

    def count_today_applied(self) -> int:
        """Count how many jobs were applied today."""
        today = datetime.now().strftime("%Y-%m-%d")
        row = self._db.conn.execute(
            """SELECT COUNT(*) as cnt FROM conversations
               WHERE decision = 'match'
                 AND created_at LIKE ?""",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"] if row else 0
