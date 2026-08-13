from __future__ import annotations

import math
from typing import Any

from .locales import DE, EN, FR, RU, ZH
from .settings import get as _get
from .settings import set_value as _set

SUPPORTED_LANGS: tuple[str, ...] = ("en", "ru", "de", "fr", "zh")

LANG_DISPLAY: dict[str, str] = {
    "en": "English",
    "ru": "Русский",
    "de": "Deutsch",
    "fr": "Français",
    "zh": "中文",
}

_DEFAULT_LANG = "en"


# ── Translation tables ──────────────────────────────────────────────────────

_TABLES: dict[str, dict[str, str]] = {
    "en": EN,
    "ru": RU,
    "de": DE,
    "fr": FR,
    "zh": ZH,
}


def get_lang() -> str:
    """Текущий язык. Невалидное значение → en."""
    lang = _get("language", _DEFAULT_LANG)
    if not isinstance(lang, str) or lang not in SUPPORTED_LANGS:
        return _DEFAULT_LANG
    return lang


def set_lang(lang: str) -> None:
    """Сохраняет язык в config. Невалидный → en."""
    if lang not in SUPPORTED_LANGS:
        lang = _DEFAULT_LANG
    _set("language", lang)


def t(key: str, **kwargs: Any) -> str:
    """Возвращает локализованную строку. Если ключа нет — fallback en → key."""
    lang = get_lang()
    table = _TABLES.get(lang, EN)
    s = table.get(key)
    if s is None:
        s = EN.get(key, key)
    if kwargs:
        try:
            return s.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return s
    return s


def format_duration(
    seconds: float,
    *,
    decimal_seconds: bool = False,
    milliseconds: bool = False,
) -> str:
    """Format a duration with localized compact hour, minute and second units.

    Whole units never overflow: 60 seconds becomes ``1m`` and 3661 seconds
    becomes ``1h 1m 1s``. ``decimal_seconds`` keeps tenths for short tool
    calls, while ``milliseconds`` gives statistics a useful sub-second value.
    """
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value):
        value = 0.0

    if milliseconds and 0 < value < 1:
        return f"{round(value * 1000):g}{t('time.unit_ms')}"

    if decimal_seconds and value < 60:
        rounded = round(value, 1)
        if 0 < rounded < 60:
            return f"{rounded:.1f}{t('time.unit_s')}"

    total = max(0, round(value))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}{t('time.unit_h')}")
    if minutes:
        parts.append(f"{minutes}{t('time.unit_m')}")
    if secs or not parts:
        parts.append(f"{secs}{t('time.unit_s')}")
    return t("time.separator").join(parts)
