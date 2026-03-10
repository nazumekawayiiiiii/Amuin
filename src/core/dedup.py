"""Deduplication — prevent processing the same job twice.

Two-layer dedup:
  Layer 1 (source): Query DB for known job_encrypt_ids before processing.
  Layer 2 (platform): chat_tool checks button text ("立即沟通" vs "继续沟通").
"""

import logging
from ..db.models import ConversationStore

log = logging.getLogger("claw")


class DedupManager:
    """Manages job deduplication using the conversations database.

    Layer 1: bulk-filters job lists against known IDs from DB.
    Layer 2 happens inside chat_tool (button text check).

    Usage:
        dedup = DedupManager(store)
        dedup.load()
        new_jobs = dedup.filter_new(job_list)
        dedup.mark_seen(job_encrypt_id)
    """

    def __init__(self, store: ConversationStore):
        self._store = store
        self._seen: set[str] = set()

    def load(self) -> int:
        """Load known job IDs from database. Returns count loaded."""
        self._seen = self._store.get_known_job_ids()
        log.info(f"[INIT] 去重: 已加载 {len(self._seen)} 个已知职位")
        return len(self._seen)

    def is_known(self, job_encrypt_id: str) -> bool:
        """Check if a job has already been processed."""
        return job_encrypt_id in self._seen

    def mark_seen(self, job_encrypt_id: str) -> None:
        """Mark a job as processed (add to in-memory set)."""
        self._seen.add(job_encrypt_id)

    def filter_new(self, job_list: list[dict]) -> list[tuple[int, dict]]:
        """Filter a job list, returning only unprocessed jobs.

        Args:
            job_list: List of job dicts from Vue jobList.

        Returns:
            List of (original_index, job_dict) for new jobs only.
        """
        new_jobs = []
        for i, job in enumerate(job_list):
            job_id = job.get("encryptJobId", "")
            if job_id and job_id not in self._seen:
                new_jobs.append((i, job))
        return new_jobs

    @property
    def known_count(self) -> int:
        return len(self._seen)
