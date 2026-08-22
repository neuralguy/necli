"""Periodic model-assisted consolidation of persistent memory."""

from __future__ import annotations

import json
import time

from config import paths
from logger import logger

from .extract import _parse_items, apply_memory_decisions
from .memdir import _is_pinned, scan_memories

INTERVAL_SECONDS = 3 * 24 * 60 * 60


def _marker_path():
    return paths.MEMORY_DIR / ".last_model_cleanup"


def cleanup_due() -> bool:
    try:
        return time.time() - _marker_path().stat().st_mtime >= INTERVAL_SECONDS
    except OSError:
        return True


def _catalog(working_dir: str | None) -> str:
    global_root = paths.global_memory_dir()
    records = [
        {
            "name": memory.name,
            "scope": "global" if memory.path.parent == global_root else "project",
            "type": memory.type,
            "created": memory.created,
            "updated": memory.updated,
            "pinned": _is_pinned(memory),
            "body": memory.body,
        }
        for memory in scan_memories(working_dir, scope="all")
    ]
    return json.dumps(records, ensure_ascii=False)


def _prompt(catalog: str) -> str:
    return (
        "Audit this long-term memory catalog. Consolidate semantic duplicates, "
        "update records whose replacement is unambiguous, and delete only clearly "
        "obsolete records. Never delete or merge away a pinned record. Prefer ignore "
        "when uncertain. Do not invent new facts. Return only a JSON array using "
        "actions update, merge, delete, or ignore. update needs target/scope/type/body; "
        "merge needs target/sources/scope/type/body; delete needs target/scope. "
        "All sources of one merge must have the same scope.\n\nCATALOG:\n" + catalog
    )


async def maybe_cleanup_memories(working_dir: str | None = None) -> int:
    """Run a best-effort cleanup at most once every three days."""
    try:
        from config.settings import get

        if not bool(get("memory_enabled", True)):
            return 0
    except Exception:
        pass
    if not cleanup_due():
        return 0
    catalog = _catalog(working_dir)
    if catalog == "[]":
        _touch_marker()
        return 0
    try:
        from apis.agent_adapter import api_extract_memory

        raw = await api_extract_memory(_prompt(catalog))
        items = [item for item in _parse_items(raw) if item.get("action") != "create"]
        changed = apply_memory_decisions(items, working_dir=working_dir)
        _touch_marker()
        logger.info("memory.cleanup: applied %d change(s)", changed)
        return changed
    except Exception as exc:
        logger.debug("memory.cleanup failed: {}", exc, exc_info=True)
        return 0


def _touch_marker() -> None:
    try:
        marker = _marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as exc:
        logger.debug("memory.cleanup marker failed: {}", exc, exc_info=True)
