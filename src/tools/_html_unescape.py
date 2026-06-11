"""Единый html-unescape для прокси-эскейпа (OnlySQ и пр.).

Прокси оборачивают сообщения и tool output html-сущностями
(" & < >). Используется в:
  - tools/call_parser.py — content/patch секции
  - apis/agent_adapter.py — стриминговый текст модели
  - agent/display.py — UI-косметика для args
"""

import html
import re

_ENTITY_HINT_RE = re.compile(r"&(?:lt|gt|amp|quot|apos|#\d+|#x[0-9a-fA-F]+);")


def has_html_entities(text: str) -> bool:
    return bool(text) and bool(_ENTITY_HINT_RE.search(text))


def maybe_unescape(text: str) -> str:
    """Декодирует HTML-сущности если они есть. Иначе возвращает как есть."""
    if not text or not _ENTITY_HINT_RE.search(text):
        return text
    return html.unescape(text)


def unescape_nested(value):
    """Рекурсивно декодирует строки внутри dict/list. Не-строки возвращает как есть."""
    if isinstance(value, str):
        return maybe_unescape(value)
    if isinstance(value, list):
        return [unescape_nested(v) for v in value]
    if isinstance(value, dict):
        return {k: unescape_nested(v) for k, v in value.items()}
    return value