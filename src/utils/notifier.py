"""Notification module — sound alerts for new messages.

Plays system bell or wav file when HR replies.
"""

import logging
import sys
import threading

log = logging.getLogger("claw")


def play_bell() -> None:
    """Play system bell sound (cross-platform)."""
    try:
        if sys.platform == "win32":
            import winsound
            # Play Windows default notification sound
            winsound.MessageBeep(winsound.MB_ICONINFORMATION)
        else:
            # Unix/Mac: terminal bell
            print("\a", end="", flush=True)
    except Exception:
        pass


def notify_new_message(msg_info: dict) -> None:
    """Trigger notification for a new HR message.

    Plays sound in a separate thread to avoid blocking.

    Args:
        msg_info: Dict with company, boss_name, last_text, etc.
    """
    threading.Thread(target=play_bell, daemon=True).start()
    log.info(
        f"[MSG] 🔔 {msg_info.get('company', '')} "
        f"{msg_info.get('boss_name', '')} 回复了你"
    )
