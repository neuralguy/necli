"""Pinned session IDs — отдельный JSON под .data/pinned_sessions.json."""

import json
import threading

from config._atomic import atomic_write_json
from config.paths import BASE_DIR
from logger import logger

_PATH = BASE_DIR / "pinned_sessions.json"
_LOCK = threading.RLock()
_load_failed = False


def _load() -> set[str]:
    global _load_failed
    if not _PATH.exists():
        _load_failed = False
        return set()
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("root must be a JSON array")
        _load_failed = False
        return {str(x) for x in data}
    except (json.JSONDecodeError, OSError, ValueError) as e:
        _load_failed = True
        logger.warning("pinned.load failed: {}", e)
        return set()


def _save(ids: set[str]) -> bool:
    if _load_failed:
        logger.error("refusing to overwrite unreadable pinned sessions file: {}", _PATH)
        return False
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(_PATH, sorted(ids))
        return True
    except OSError as e:
        logger.error("pinned.save failed: {}", e)
        return False


def get_pinned() -> set[str]:
    return _load()


def toggle(sid: str) -> bool:
    """Toggle pin для session_id. Возвращает новое состояние (True = pinned)."""
    with _LOCK:
        ids = _load()
        if _load_failed:
            return sid in ids
        if sid in ids:
            ids.discard(sid)
            return False if _save(ids) else True
        ids.add(sid)
        return True if _save(ids) else False
