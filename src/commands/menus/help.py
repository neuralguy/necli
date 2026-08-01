"""Интерактивная справка по slash-командам.

Раньше экран рисовался тремя вложенными `Panel` — то есть тремя рамками внутри
рамки ввода. Заказчик просил линии убрать: теперь это две колонки из пробелов,
табы категорий приглушённым цветом и подсветка выбранной команды фоном
`bg_select`. Кирпичи — общие, из `ui/overlays.py`.

Гайд по команде переносится по словам вручную (`textwrap`), а не Rich'ем:
виджет собирается как готовая ANSI-строка, и лишний проход Rich на каждом кадре
тикера здесь не нужен.
"""

from __future__ import annotations

import textwrap

from config.i18n import t as _
from ui.menu import Palette, overlay_rows
from ui.overlays import (
    BOLD,
    DIM,
    RESET,
    WHITE,
    clip,
    pad,
    paint,
    role_bg,
    spacer,
)
from ui.shell import Overlay, get_shell, print_static

_SECTIONS = ("why", "usage", "examples", "tip")


def _guide_sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("@") and line[1:] in _SECTIONS:
            sections.append((line[1:], []))
        elif sections:
            sections[-1][1].append(line)
    return sections


class HelpOverlay(Overlay):
    """Табы категорий сверху, список команд слева, гайд справа. ←→ таб, ↑↓ команда."""

    def __init__(self, groups: list[tuple[str, list]]) -> None:
        super().__init__()
        self.groups = groups
        self.category = 0
        self.command = 0
        self.left_width = max(
            len(c.name) + len(c.args_hint) + 1
            for _key, commands in groups for c in commands
        ) + 2
        self._guide_cache: dict[tuple[str, int], list[str]] = {}

    # ── содержимое ──
    def _guide_lines(self, command_name: str, width: int) -> list[str]:
        """Готовые строки гайда: заголовки секций акцентом, текст — по словам.

        Кэшируем по (команда, ширина): перенос по словам на каждом кадре
        тикера — ровно та работа, из-за которой большие виджеты и тормозили.
        """
        key = (command_name, width)
        cached = self._guide_cache.get(key)
        if cached is not None:
            return cached
        out: list[str] = []
        for index, (section, body) in enumerate(
                _guide_sections(_(f"help.guide.{command_name[1:]}"))):
            if index:
                out.append("")
            out.append(paint(_(f"help.section.{section}"), "accent", bold=True))
            for raw in "\n".join(body).rstrip().split("\n"):
                if not raw.strip():
                    out.append("")
                    continue
                out.extend(textwrap.wrap(raw, width=max(12, width),
                                         subsequent_indent="  ") or [""])
        self._guide_cache[key] = out
        return out

    def _tabs(self, width: int) -> str:
        parts = []
        for index, (key, _commands) in enumerate(self.groups):
            label = _(key)
            parts.append(paint(f" {label} ", "accent", bold=True) if index == self.category
                         else f"{DIM} {label} {RESET}")
        return "  " + clip("".join(parts), max(8, width - 4))

    def render(self, width: int) -> str:
        pal = Palette()
        _key, commands = self.groups[self.category]
        self.command = min(self.command, len(commands) - 1)

        left_w = min(self.left_width, max(12, width // 3))
        guide_w = max(16, width - left_w - 8)
        guide = self._guide_lines(commands[self.command].name, guide_w)

        budget = max(4, overlay_rows())
        body_rows = max(1, budget - 2)          # табы + пустая строка
        rows = min(max(len(commands), len(guide)), body_rows)
        sel_bg = role_bg("bg_select")

        lines = [self._tabs(width), spacer()]
        for i in range(rows):
            if i < len(commands):
                item = commands[i]
                label = pad(f"{item.name} {item.args_hint}".rstrip(), left_w)
                label = clip(label, left_w)
                if i == self.command:
                    left = f"  {BOLD}{pal.accent}❯{RESET} {sel_bg}{BOLD}{WHITE}{label}{RESET}"
                else:
                    left = f"    {label}"
            else:
                left = " " * (left_w + 4)
            right = guide[i] if i < len(guide) else ""
            lines.append(f"{left}  {right}" if right else left.rstrip())
        hidden = max(len(commands), len(guide)) - rows
        if hidden > 0:
            lines.append(f"    {DIM}… {hidden}{RESET}")
        return "\n".join(lines)

    def hint(self) -> str:
        return _("help.menu_hint")

    def handle_key(self, key: str, event) -> bool:
        if key in ("escape", "c-c"):
            self.finish(None)
            return True
        commands = self.groups[self.category][1]
        if key == "left":
            self.category = (self.category - 1) % len(self.groups)
            self.command = 0
        elif key == "right":
            self.category = (self.category + 1) % len(self.groups)
            self.command = 0
        elif key == "up":
            self.command = (self.command - 1) % len(commands)
        elif key == "down":
            self.command = (self.command + 1) % len(commands)
        return True

    def version(self):
        """Кроме курсора и таба тут ничего не меняется — кэш кадра переживает
        простой, и Shell не пересобирает справку 20 раз в секунду."""
        return (self.category, self.command)


async def help_interactive() -> None:
    """Показывает справку и обрабатывает стрелки до Esc/Ctrl+C."""
    from commands.registry import by_category

    groups = [(key, cmds) for _id, key, cmds in by_category() if cmds]
    if not groups:
        return

    overlay = HelpOverlay(groups)
    shell = get_shell()
    if shell is None:
        # Нет Application (не-TTY, headless) — читать клавиши нечем, поэтому
        # просто печатаем первый кадр справки и выходим.
        from ui.menu import render_width
        print_static(overlay.render(render_width()))
        return
    await shell.run_overlay(overlay)


__all__ = ["HelpOverlay", "help_interactive"]
