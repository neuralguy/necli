from rich.console import Console

from config.i18n import (
    LANG_DISPLAY,
    SUPPORTED_LANGS,
    get_lang,
    set_lang,
    t,
)
from ui.menu import select_menu

console = Console()


def lang_interactive() -> None:
    current = get_lang()
    items = []
    for code in SUPPORTED_LANGS:
        items.append({  # noqa: PERF401
            "label": LANG_DISPLAY.get(code, code),
            "hint": code,
            "active": code == current,
        })
    items.append({"label": t("common.back"), "hint": ""})

    choice = select_menu(items, title=t("lang.subtitle"))
    if choice is None or choice == len(SUPPORTED_LANGS):
        return

    code = SUPPORTED_LANGS[choice]
    if code != current:
        set_lang(code)
        console.print(
            f"  [green]✓[/green] {t('lang.changed', name=LANG_DISPLAY.get(code, code))}"
        )
