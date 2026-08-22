"""Unified /settings menu.

The header owns horizontal section navigation.  The content owns vertical item
navigation.  Focus is deliberately exclusive: arrows never move both axes at
once, which keeps the two-level menu predictable in a terminal.
"""

from __future__ import annotations

import inspect

from commands.menus._editor import open_in_editor
from commands.menus._style import card_menu
from config.i18n import t as tr
from config.paths import USER_PROMPT_FILE
from ui.overlays import clip, key_hints, paint, row, spacer
from ui.shell import Overlay, get_shell

_TRAILING_HINT_PUNCTUATION = ".。!;"


def _item_text(label_key: str, hint_key: str) -> tuple[str, str]:
    """Normalize settings rows and avoid label/hint duplication."""
    label = tr(label_key).strip()
    hint = tr(hint_key).strip().rstrip(_TRAILING_HINT_PUNCTUATION).strip()
    folded_label = label.casefold().rstrip(":：")
    folded_hint = hint.casefold()
    if folded_hint == folded_label:
        hint = ""
    elif folded_hint.startswith(folded_label):
        rest = hint[len(label) :].lstrip(" :：-—–·")
        if rest:
            hint = rest
    return label, (f"- {hint}" if hint else "")


_SECTIONS = (
    (
        "model",
        "◈",
        (
            ("helpers", "helpers.title", "help.helpers"),
            ("params", "params.title", "help.params"),
            ("prompt", "prompt.title", "prompt.hint"),
        ),
    ),
    (
        "tools",
        "⌘",
        (
            ("lsp", "lsp.title", "help.lsp"),
            ("mcp", "mcp.title", "help.mcp"),
        ),
    ),
    (
        "interface",
        "◫",
        (
            ("lang", "lang.subtitle", "help.lang"),
            ("notifications", "notifications.title", "help.notifications"),
            ("display", "display.title", "display.hint"),
        ),
    ),
    (
        "memory",
        "⋯",
        (("memory", "memory.title", "memory.hint"),),
    ),
)


class SettingsOverlay(Overlay):
    """Two-axis settings navigator with an explicit header/content focus."""

    def __init__(self, section: int = 0, item: int = 0) -> None:
        super().__init__()
        self.section = max(0, min(section, len(_SECTIONS) - 1))
        self.item = max(0, item)
        self.focus = "header"
        self._clamp_item()

    def _items(self):
        return _SECTIONS[self.section][2]

    def _clamp_item(self) -> None:
        self.item = max(0, min(self.item, max(0, len(self._items()) - 1)))

    def render(self, width: int) -> str:
        lines = [paint(f"⚙︎ {tr('settings.title')}", "accent", bold=True)]

        tabs: list[str] = []
        for i, (key, icon, _items) in enumerate(_SECTIONS):
            active = i == self.section
            focused = active and self.focus == "header"
            marker = "❯ " if focused else "  "
            label = f"{marker}{icon} {tr(f'settings.section.{key}')}"
            tabs.append(paint(label, "accent" if active else "dim_text", bold=active))
        lines.append(clip("  ".join(tabs), max(8, width - 1)))
        lines.append(spacer())

        for i, (_action, label_key, hint_key) in enumerate(self._items()):
            label, hint = _item_text(label_key, hint_key)
            lines.append(
                row(
                    label,
                    hint,
                    selected=self.focus == "content" and i == self.item,
                    width=width,
                )
            )
        return "\n".join(lines)

    def hint(self) -> str:
        if self.focus == "header":
            return key_hints(
                ("←→", tr("settings.switch_section")),
                ("↓/Enter", tr("settings.enter_section")),
                ("Esc", tr("common.cancel")),
            )
        return key_hints(
            ("↑↓", tr("settings.choose")),
            ("Enter", tr("settings.open")),
            ("Esc/Tab", tr("settings.header")),
        )

    def version(self):
        return self.section, self.item, self.focus

    def handle_key(self, key: str, event) -> bool:
        del event
        if self.focus == "header":
            if key in ("left", "h"):
                self.section = (self.section - 1) % len(_SECTIONS)
                self.item = 0
            elif key in ("right", "l"):
                self.section = (self.section + 1) % len(_SECTIONS)
                self.item = 0
            elif key in ("down", "j", "enter", "tab"):
                self.focus = "content"
                self._clamp_item()
            elif key in ("escape", "c-c", "q", "Q"):
                self.finish(None)
            return True

        if key in ("up", "k"):
            if self.item == 0:
                self.focus = "header"
            else:
                self.item -= 1
        elif key in ("down", "j"):
            self.item = min(len(self._items()) - 1, self.item + 1)
        elif key == "enter":
            self.finish((self.section, self.item, self._items()[self.item][0]))
        elif key in ("escape", "tab"):
            self.focus = "header"
        elif key in ("c-c", "q", "Q"):
            self.finish(None)
        return True


async def _open_action(action: str) -> None:
    if action == "prompt":
        from commands.menus.prompt import prompt_interactive

        result = prompt_interactive()
    elif action == "role_prompt":
        USER_PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        USER_PROMPT_FILE.touch(exist_ok=True)
        await open_in_editor(str(USER_PROMPT_FILE))
        return
    elif action == "memory":
        from commands.menus.memory import memory_interactive

        result = memory_interactive()
    elif action == "helpers":
        from commands.menus.helpers import helpers_interactive

        result = helpers_interactive()
    elif action == "params":
        from commands.menus.params import params_interactive

        result = params_interactive()
    elif action == "lsp":
        from commands.menus.lsp import lsp_interactive

        result = lsp_interactive()
    elif action == "mcp":
        from commands.menus.mcp import mcp_interactive

        result = mcp_interactive()
    elif action == "lang":
        from commands.menus.lang import lang_interactive

        result = lang_interactive()
    elif action == "notifications":
        from commands.menus.notifications import notifications_interactive

        result = notifications_interactive()
    elif action == "display":
        from commands.menus.display import display_interactive

        result = display_interactive()
    else:
        return
    if inspect.isawaitable(result):
        await result


async def settings_interactive() -> None:
    section = 0
    item = 0
    shell = get_shell()

    if shell is None:
        while True:
            sections = [
                {"label": tr(f"settings.section.{key}"), "icon": icon}
                for key, icon, _items in _SECTIONS
            ]
            section_choice = await card_menu(sections, title=tr("settings.title"))
            if section_choice is None:
                return
            section = section_choice
            items = []
            for _action, label_key, hint_key in _SECTIONS[section][2]:
                label, hint = _item_text(label_key, hint_key)
                items.append({"label": label, "hint": hint})
            item_choice = await card_menu(items, title=tr("settings.title"))
            if item_choice is None:
                continue
            item = item_choice
            await _open_action(_SECTIONS[section][2][item][0])
        return

    while True:
        result = await shell.run_overlay(SettingsOverlay(section, item))
        if result is None:
            return
        section, item, action = result
        await _open_action(action)
