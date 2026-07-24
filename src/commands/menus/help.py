"""Интерактивная справка по slash-командам."""

from __future__ import annotations

import sys
from io import StringIO

from rich.console import Console, Group
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from config.i18n import t as _
from config.themes import t as _theme
from logger import logger
from ui._keyreader import drain_keys, raw_mode
from ui.menu import _clear_stream_lines, _move_up_and_overwrite, _physical_rows

_SECTIONS = ("why", "usage", "examples", "tip")


def _guide_sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("@") and line[1:] in _SECTIONS:
            sections.append((line[1:], []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def help_interactive() -> None:
    """Рисует справку и обрабатывает стрелки до Esc/Ctrl+C."""
    from commands.registry import by_category

    groups = [(key, cmds) for _id, key, cmds in by_category() if cmds]
    if not groups:
        return

    stream = sys.stderr
    category = command = 0
    accent = _theme("accent")
    selected = Style(bgcolor=_theme("bg_select"), bold=True, color="white")
    accent_style = Style(color=accent, bold=True)
    left_width = max(
        len(c.name) + len(c.args_hint) + 1
        for _key, commands in groups for c in commands
    ) + 4
    render_width = max(48, Console().size.width - 1)

    def guide_text_for(command_name: str) -> Text:
        guide_text = Text()
        for section_index, (section, section_lines) in enumerate(
            _guide_sections(_(f"help.guide.{command_name[1:]}"))
        ):
            if section_index:
                guide_text.append("\n")
            guide_text.append(_(f"help.section.{section}") + "\n", accent_style)
            guide_text.append("\n".join(section_lines).rstrip() + "\n")
        guide_text.highlight_regex(r"/[a-z_][a-z0-9_]*", "bold")
        guide_text.rstrip()
        return guide_text

    # Все панели гайдов имеют высоту самого длинного, включая переносы текста.
    # ponytail: при очень низком терминале меню может выйти за экран; добавить
    # scroll только если гайды станут длиннее доступной высоты.
    guide_width = max(20, render_width - left_width - 10)
    measure_console = Console(width=guide_width, force_terminal=True, highlight=False)
    guide_height = max(
        len(measure_console.render_lines(guide_text_for(item.name))) + 2
        for _key, commands in groups for item in commands
    )

    def render() -> str:
        nonlocal command
        _category_key, commands = groups[category]
        command = min(command, len(commands) - 1)
        current = commands[command]

        tabs = Text(" ")
        for index, (key, _commands) in enumerate(groups):
            if index:
                tabs.append("  ")
            label = f" {_(key)} "
            if index == category:
                tabs.append(label, accent_style)
            else:
                tabs.append(label, Style(dim=True))

        command_list = Text()
        for index, item in enumerate(commands):
            label = f"{item.name} {item.args_hint}".rstrip()
            is_selected = index == command
            command_list.append(
                (("❯ " if is_selected else "  ") + label).ljust(left_width),
                selected if is_selected else Style(),
            )
            command_list.append("\n")

        guide = Panel(
            guide_text_for(current.name), title=current.name, title_align="left",
            border_style=accent, padding=(0, 1), height=guide_height,
        )

        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(width=left_width + 1, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(command_list, guide)
        frame = Panel(
            Group(tabs, Text(""), grid, Text(""), Text(_("help.menu_hint"), style="dim")),
            title=_("help.title"), title_align="left", border_style="dim", padding=(0, 1),
        )
        output = StringIO()
        Console(file=output, force_terminal=True, highlight=False,
                width=max(48, Console().size.width - 1)).print(frame)
        return output.getvalue()

    content = render()
    term_width = Console().size.width
    lines = sum(_physical_rows(line, term_width) for line in content.split("\n")) or 1
    stream.write("\x1b[?25l" + content)
    stream.flush()
    try:
        with raw_mode():
            while True:
                key = drain_keys()
                if key in ("escape", "ctrl-c"):
                    break
                if key == "left":
                    category = (category - 1) % len(groups)
                elif key == "right":
                    category = (category + 1) % len(groups)
                elif key == "up":
                    command = (command - 1) % len(groups[category][1])
                elif key == "down":
                    command = (command + 1) % len(groups[category][1])
                else:
                    continue
                lines = _move_up_and_overwrite(stream, render(), lines)
    except Exception:
        logger.exception("help menu failed")
    finally:
        _clear_stream_lines(stream, lines)
        stream.write("\x1b[?25h")
        stream.flush()
