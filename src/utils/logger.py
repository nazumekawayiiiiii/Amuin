"""UI-integrated logging — thread-safe log forwarding to GUI.

Uses a queue-based approach: background threads write to a queue,
the UI polls the queue and displays log entries.
"""

import logging
import queue
from typing import Callable


class QueueLogHandler(logging.Handler):
    """Logging handler that puts records into a thread-safe queue.

    The UI polls this queue via get_pending() and renders entries.
    """

    def __init__(self, maxsize: int = 5000):
        super().__init__()
        self._queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=maxsize)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            # Drop oldest to make room
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(record)
            except queue.Full:
                pass

    def get_pending(self) -> list[logging.LogRecord]:
        """Drain all pending records (non-blocking). Called from UI thread."""
        records = []
        while True:
            try:
                records.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return records


class CallbackLogHandler(logging.Handler):
    """Logging handler that calls a callback for each record.

    Useful for status bar updates or notification triggers.
    """

    def __init__(self, callback: Callable[[logging.LogRecord], None]):
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(record)
        except Exception:
            pass


def setup_logging(queue_handler: QueueLogHandler | None = None) -> QueueLogHandler:
    """Configure root 'claw' logger with console + queue handlers.

    Args:
        queue_handler: Existing handler to reuse, or None to create one.

    Returns:
        The QueueLogHandler instance (pass to UI for polling).
    """
    if queue_handler is None:
        queue_handler = QueueLogHandler()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    # Queue handler (for UI)
    queue_handler.setFormatter(formatter)

    # Configure root claw logger
    logger = logging.getLogger("claw")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(console)
    logger.addHandler(queue_handler)

    return queue_handler
