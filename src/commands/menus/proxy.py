"""Меню /proxy: текущий прокси и его правка.

Текущее значение — шапка виджета; факт изменения показывается динамически.
"""

import config
from apis.agent_adapter import invalidate_api_llm
from commands.menus._style import card_menu, facts_line
from config import t as _
from ui import overlays

# Допустимые схемы прокси (см. settings.py["proxy"]).
_VALID_SCHEMES = ("http://", "https://", "socks5://", "socks5h://", "socks4://")


def _validate(url: str) -> bool:
    return url.lower().startswith(_VALID_SCHEMES)


def _validate_field(raw: str) -> str | None:
    """Обёртка для ask_text: пустая строка = отмена, схему проверяем на месте,
    чтобы ошибка показалась в поле, а меню не закрывалось."""
    if not raw:
        return None
    return None if _validate(raw) else _("proxy.invalid")


async def proxy_interactive() -> None:
    while True:
        current = str(config.get("proxy", "") or "")
        shown = current or _("proxy.none")

        items = [
            {"label": _("proxy.set"), "hint": _("proxy.set_hint"), "icon": "✎",
             "icon_style": "accent"},
            {"label": _("proxy.clear"), "hint": _("proxy.clear_hint"), "icon": "✗",
             "icon_style": "error"},
            {"label": _("common.back"), "icon": " "},
        ]
        choice = await card_menu(
            items,
            title=_("proxy.title"),
            status=shown,
            status_style="warning" if current else "muted",
            facts=[facts_line(_("proxy.header"), _("proxy.schemes_hint"))],
        )
        if choice is None or choice == 2:
            return

        if choice == 0:
            raw = await overlays.ask_text(
                f"{_('proxy.enter')} (http://… / socks5://…):",
                validate=_validate_field,
            )
            if not raw:
                continue
            config.set_value("proxy", raw)
            invalidate_api_llm()
            continue

        if choice == 1:
            config.set_value("proxy", "")
            invalidate_api_llm()
            continue
