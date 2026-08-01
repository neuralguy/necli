"""Интерактивное меню /autoprune — настройка режима управления контекстом
для провайдеров БЕЗ prompt caching.

Меню позволяет вкл/выкл каждый пункт autoprune отдельно и менять значения
(число раундов сжатия, порог токенов, число раундов сворачивания tool-выводов).
Мастер-переключатель — ползунок prompt cache у провайдера: когда кэш ON,
autoprune неактивен, о чём сообщает статус в шапке виджета (раньше это
печаталось в scrollback при каждом заходе в меню).
"""

import config
from commands.menus._style import card_menu
from config import t as _
from logger import logger
from ui import overlays

# (ключ настройки, ключ label, ключ hint) — toggle-пункты меню.
_TOGGLES = [
    ("autoprune_file_dedup", "autoprune.file_dedup", "autoprune.file_dedup_hint"),
    ("autoprune_tool_folding", "autoprune.tool_folding", "autoprune.tool_folding_hint"),
    ("autoprune_round_compression", "autoprune.round_compression",
     "autoprune.round_compression_hint"),
    ("autoprune_safety_compress", "autoprune.safety_compress",
     "autoprune.safety_compress_hint"),
]

# (ключ настройки, ключ label) — value-пункты меню.
_VALUES = [
    ("autoprune_compress_every_rounds", "autoprune.compress_every_rounds"),
    ("autoprune_compress_at_tokens", "autoprune.compress_at_tokens"),
    ("autoprune_tool_fold_rounds", "autoprune.tool_fold_rounds"),
]


def _autoprune_active() -> bool:
    """True когда autoprune активен (у активного провайдера выключен prompt cache)."""
    try:
        from apis.agent_adapter import get_api_session
        sess = get_api_session()
        if sess is None or sess.llm is None:
            return False
        return not sess.llm._supports_anthropic_cache_control()
    except Exception:
        return False


def _fmt_value(key: str) -> str:
    v = config.get(key)
    if key == "autoprune_compress_at_tokens" and isinstance(v, int):
        return f"{v:,}".replace(",", " ")
    return str(v)


# Пусто = отказ от правки; иначе только целое > 0. Проверка переехала из кода
# после ввода в сам оверлей: ошибка видна в поле, значение можно поправить.
def _validate_positive_int(raw: str) -> str | None:
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        return _("autoprune.invalid_int")
    return None if val > 0 else _("autoprune.invalid_int")


async def autoprune_interactive() -> None:
    active = _autoprune_active()

    while True:
        items = []
        for key, lkey, hkey in _TOGGLES:
            on = bool(config.get(key, True))
            items.append({
                "icon": "✓" if on else "✗",
                "icon_style": "success" if on else "error",
                "label": _(lkey),
                "hint": _(hkey) if hkey else "",
            })
        for key, lkey in _VALUES:
            items.append({
                "icon": "✎",
                "icon_style": "dim",
                "label": _(lkey),
                "badge": _fmt_value(key),
                "badge_style": "warning",
            })
        items.append({"icon": " ", "label": _("common.back")})

        choice = await card_menu(
            items,
            title=_("autoprune.title"),
            status="● on" if active else "○ off",
            status_style="success" if active else "warning",
            # Полное объяснение — строкой факта: в правом углу заголовка оно
            # съедало бы сам заголовок.
            facts=[_("autoprune.active") if active else _("autoprune.inactive")],
        )
        if choice is None or choice == len(items) - 1:
            return

        # Toggle-пункты.
        if choice < len(_TOGGLES):
            key, lkey, _hkey = _TOGGLES[choice]
            new_val = not bool(config.get(key, True))
            config.set_value(key, new_val)
            logger.info("autoprune toggle: %s → %s", key, new_val)
            continue

        # Value-пункты.
        vi = choice - len(_TOGGLES)
        if vi < len(_VALUES):
            key, lkey = _VALUES[vi]
            raw = await overlays.ask_text(
                f"{_(lkey)} ({_('autoprune.enter_value', name=_(lkey))}):",
                default=_fmt_value(key).replace(" ", ""),
                validate=_validate_positive_int,
            )
            if not raw:
                continue
            config.set_value(key, int(raw))
            logger.info("autoprune value: %s → %s", key, raw)
