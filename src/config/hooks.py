"""Загрузка конфигурации hooks из .data/hooks.json.

Кэшируется по mtime файла — повторные чтения дешёвые, но правки файла
подхватываются без рестарта.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from hooks.schema import HOOK_EVENTS, HookMatcher, HookSpec

from .paths import HOOKS_FILE

logger = logging.getLogger(__name__)

_cache: dict[str, list[HookMatcher]] | None = None
_cache_mtime: float = -1.0


def _normalize_event_value(raw: Any) -> list[HookMatcher]:
    """Принимает как формат с matcher-обёрткой, так и плоский список hooks."""
    if not isinstance(raw, list):
        return []
    matchers: list[HookMatcher] = []
    flat: list[HookSpec] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if "hooks" in item:
            try:
                matchers.append(HookMatcher.from_dict(item))
            except (ValueError, KeyError, TypeError) as e:
                logger.warning("hooks: skipping bad matcher: %s", e)
        elif "type" in item or "command" in item or "url" in item:
            # Плоский hook без обёртки — собираем под matcher '*'.
            try:
                flat.append(HookSpec.from_dict(item))
            except (ValueError, KeyError, TypeError) as e:
                logger.warning("hooks: skipping bad hook: %s", e)
    if flat:
        matchers.append(HookMatcher(matcher="*", hooks=flat))
    return matchers


def load_hooks() -> dict[str, list[HookMatcher]]:
    """Возвращает {event: [HookMatcher, ...]} из hooks.json (с кэшем по mtime)."""
    global _cache, _cache_mtime
    try:
        mtime = HOOKS_FILE.stat().st_mtime
    except OSError:
        _cache, _cache_mtime = {}, -1.0
        return {}

    if _cache is not None and mtime == _cache_mtime:
        return _cache

    try:
        data = json.loads(HOOKS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("hooks: failed to load %s: %s", HOOKS_FILE, e)
        _cache, _cache_mtime = {}, mtime
        return {}

    result: dict[str, list[HookMatcher]] = {}
    if isinstance(data, dict):
        # Допускаем верхнюю обёртку {"hooks": {...}} как в claude-code settings.
        events = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
        for event, raw in (events or {}).items():
            if event not in HOOK_EVENTS:
                continue
            matchers = _normalize_event_value(raw)
            if matchers:
                result[event] = matchers

    _cache, _cache_mtime = result, mtime
    return result


def has_hooks(event: str | None = None) -> bool:
    cfg = load_hooks()
    if event is None:
        return bool(cfg)
    return bool(cfg.get(event))


def invalidate_cache() -> None:
    """Сбрасывает кэш разобранных hooks (после изменения hooks.json)."""
    global _cache, _cache_mtime
    _cache, _cache_mtime = None, -1.0
