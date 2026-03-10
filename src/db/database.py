"""SQLite database management.

Handles connection lifecycle, table creation, and automatic
cleanup of records older than 15 days.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta

log = logging.getLogger("claw")

RETENTION_DAYS = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_encrypt_id      TEXT UNIQUE NOT NULL,
    boss_encrypt_id     TEXT,
    company             TEXT,
    position            TEXT,
    salary_range        TEXT,
    city                TEXT,
    status              TEXT DEFAULT 'active',
    score               INTEGER,
    score_reason        TEXT,
    decision            TEXT,
    greeting_used       TEXT,
    reply_templates     TEXT,
    last_msg_time       TEXT,
    last_msg_from       TEXT,
    followup_count      INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_job_id
    ON conversations(job_encrypt_id);

CREATE INDEX IF NOT EXISTS idx_conversations_status
    ON conversations(status);

CREATE INDEX IF NOT EXISTS idx_conversations_score
    ON conversations(score DESC);
"""


class Database:
    """SQLite database wrapper for Claw.

    Usage:
        db = Database(Path("./data/claw.db"))
        db.connect()
        # ... use db ...
        db.close()
    """

    def __init__(self, db_path: Path):
        self._path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Open connection and ensure schema exists."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        log.info(f"[INIT] 数据库已连接: {self._path}")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def cleanup_expired(self) -> int:
        """Delete records older than RETENTION_DAYS. Returns count deleted."""
        cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM conversations WHERE created_at < ?", (cutoff,)
        )
        self._conn.commit()
        count = cursor.rowcount
        if count:
            log.info(f"[INIT] 清理 {count} 条过期记录（>{RETENTION_DAYS}天）")
        return count

    @property
    def conn(self) -> sqlite3.Connection:
        if not self._conn:
            raise RuntimeError("Database not connected")
        return self._conn
