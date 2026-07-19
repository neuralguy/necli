"""In-memory кэш полных tool outputs для expand_tool_result.

Когда _build_result_message режет длинный output, полный текст
кладётся сюда под коротким id. Модель может запросить полный
текст через tool expand_tool_result {"id": "..."}.

Кэш ограничен по количеству записей (FIFO).
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

_MAX_ENTRIES = 200

_cache: OrderedDict[str, str] = OrderedDict()


def _make_id(text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return h[:10]


def store(text: str) -> str:
    """Кладёт полный текст в кэш и возвращает id."""
    rid = _make_id(text)
    if rid in _cache:
        _cache.move_to_end(rid)
        return rid
    _cache[rid] = text
    while len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)
    return rid


def get(rid: str) -> str | None:
    """Возвращает полный текст по id или None."""
    if rid in _cache:
        _cache.move_to_end(rid)
        return _cache[rid]
    return None


def size() -> int:
    return len(_cache)
