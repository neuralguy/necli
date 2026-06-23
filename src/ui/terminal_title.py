"""Terminal window title helpers."""

import os
import sys

_STATUS_EMOJI = {
    "idle": "💤",
    "working": "⚙️",
    "poll": "❓",
    "done": "✅",
}
_CURRENT_STATUS = "idle"


def set_activity_status(status: str) -> str:
    global _CURRENT_STATUS
    if status not in _STATUS_EMOJI:
        status = "idle"
    _CURRENT_STATUS = status
    return status


def activity_emoji(status: str | None = None) -> str:
    return _STATUS_EMOJI.get(status or _CURRENT_STATUS, _STATUS_EMOJI["idle"])


def set_terminal_title(title: str) -> None:
    if not title:
        return
    out = getattr(sys, "__stdout__", None) or sys.stdout
    if out is None:
        return
    isatty = getattr(out, "isatty", None)
    if not callable(isatty) or not isatty():
        return
    if os.environ.get("TERM") == "dumb":
        return
    safe = str(title).replace("\x1b", "").replace("\x07", "").replace("\n", " ").strip()
    if not safe:
        return
    try:
        out.write(f"\033]0;{safe}\007")
        out.flush()
    except OSError:
        return

def reset_terminal_title() -> None:
    set_terminal_title("Терминал")


def session_title(session, status: str | None = None) -> str:
    title = (getattr(session, "title", "") or "").strip()
    sid = (getattr(session, "id", "") or "").strip()
    return f"{activity_emoji(status)} necli — {title or sid or 'session'}"


def set_session_terminal_title(session, status: str | None = None) -> None:
    if status is not None:
        set_activity_status(status)
    set_terminal_title(session_title(session, status))