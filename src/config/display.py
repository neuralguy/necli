"""Per-block display expansion settings for compact and full views."""

from __future__ import annotations

from config import settings

COMPACT_KEY = "display_full_blocks_compact"
FULL_KEY = "display_full_blocks_full"

DEFAULT_FULL_BLOCKS: list[str] = ["*"]


def _selected(key: str, default: list[str]) -> set[str]:
    value = settings.get(key, default)
    if not isinstance(value, list):
        return set(default)
    return {str(item) for item in value if isinstance(item, str) and item.strip()}


def get_full_blocks(*, compact: bool) -> set[str]:
    key = COMPACT_KEY if compact else FULL_KEY
    default = [] if compact else DEFAULT_FULL_BLOCKS
    return _selected(key, default)


def is_block_full(block_name: str, *, compact: bool) -> bool:
    """Whether a named tool/thought block gets its complete content."""
    name = str(block_name or "").strip()
    selected = get_full_blocks(compact=compact)
    return "*" in selected or name in selected


def set_full_blocks(*, compact: bool, blocks: set[str] | list[str]) -> None:
    key = COMPACT_KEY if compact else FULL_KEY
    settings.set_value(
        key, sorted({str(block) for block in blocks if str(block).strip()})
    )
