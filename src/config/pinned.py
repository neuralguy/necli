"""Pinned session IDs — отдельный JSON под .data/pinned_sessions.json."""

import json
import threading

from config._atomic import atomic_write_json
from config.paths import BASE_DIR
from logger import logger

_PATH = BASE_DIR / "pinned_sessions.json"
_LOCK = threading.RLock()


def _load() -> set[str]:
    if not _PATH.exists():
        return set()
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
        return set()
    except Exception as e:
        logger.warning("pinned.load failed: {}", e)
        return set()


def _save(ids: set[str]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(_PATH, sorted(ids))
    except Exception as e:
        logger.error("pinned.save failed: {}", e)


def get_pinned() -> set[str]:
    return _load()


def toggle(sid: str) -> bool:
    """Toggle pin для session_id. Возвращает новое состояние (True = pinned)."""
    with _LOCK:
        ids = _load()
        if sid in ids:
            ids.discard(sid)
            _save(ids)
            return False
        ids.add(sid)
        _save(ids)
        return True
