"""Меню /lang: выбор языка интерфейса.

Выбор языка — терминальное действие: применяем и закрываем меню.
"""

from commands.menus._style import card_menu
from config.i18n import (
    LANG_DISPLAY,
    SUPPORTED_LANGS,
    get_lang,
    set_lang,
    t,
)


async def lang_interactive() -> None:
    current = get_lang()
    items = [
        {"label": LANG_DISPLAY.get(code, code), "hint": code, "active": code == current}
        for code in SUPPORTED_LANGS
    ]
    items.append({"label": t("common.back")})

    choice = await card_menu(
        items,
        title=t("lang.subtitle"),
        current=SUPPORTED_LANGS.index(current) if current in SUPPORTED_LANGS else 0,
    )
    if choice is None or choice == len(SUPPORTED_LANGS):
        return

    code = SUPPORTED_LANGS[choice]
    if code != current:
        set_lang(code)
