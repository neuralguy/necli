"""Interactive management of the user role prompt from ``/settings``."""

from __future__ import annotations

from commands.menus._editor import open_in_editor
from commands.menus._style import card_menu
from config.i18n import t as _
from config.paths import USER_PROMPT_FILE
from config.settings import get, set_value


def prompt_enabled() -> bool:
    return bool(get("user_prompt_enabled", True))


def set_prompt_enabled(enabled: bool) -> None:
    set_value("user_prompt_enabled", bool(enabled))


def prompt_preview() -> list[str]:
    try:
        lines = USER_PROMPT_FILE.read_text(encoding="utf-8").splitlines()[:5]
    except (OSError, UnicodeError):
        lines = []
    return lines or [_("prompt.empty")]


async def prompt_interactive() -> None:
    while True:
        enabled = prompt_enabled()
        choice = await card_menu(
            [
                {
                    "label": _("prompt.disable") if enabled else _("prompt.enable"),
                    "hint": _("prompt.toggle_hint"),
                    "icon": "●" if enabled else "○",
                    "icon_style": "success" if enabled else "muted",
                    "action": True,
                },
                {
                    "label": _("prompt.edit"),
                    "hint": _("prompt.edit_hint"),
                    "action": True,
                },
                {"label": _("common.back")},
            ],
            title=_("prompt.title"),
            status=_("prompt.enabled") if enabled else _("prompt.disabled"),
            status_style="success" if enabled else "muted",
            facts=[_("prompt.preview"), *prompt_preview()],
            expand=True,
        )
        if choice is None or choice == 2:
            return
        if choice == 0:
            set_prompt_enabled(not enabled)
        elif choice == 1:
            USER_PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
            USER_PROMPT_FILE.touch(exist_ok=True)
            await open_in_editor(str(USER_PROMPT_FILE))


__all__ = [
    "prompt_enabled",
    "prompt_interactive",
    "prompt_preview",
    "set_prompt_enabled",
]
