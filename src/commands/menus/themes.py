import re
import shutil
import sys
from io import StringIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
from config.themes import (
    get_theme, set_theme, set_custom_color, reset_custom,
    get_active_theme_name, has_custom_overrides, list_themes,
    BUILTIN_THEMES, ROLES, ROLE_LABELS,
)
from config.i18n import t as _
from ui.menu import _panel_menu_direct
from agent.theme_preview import render_theme_preview

console = Console()


_SWATCH_ROLES = ("accent", "success", "warning", "error", "info", "magenta", "purple")


def _build_theme_list_panel(theme_names: list[str], selected: int,
                            current: str, custom: bool, width: int) -> str:
    """Рендерит панель со списком тем (без превью)."""
    table = Table(
        show_header=True,
        header_style="bold dim",
        border_style="dim",
        padding=(0, 1),
        show_edge=False,
        show_lines=False,
        expand=True,
    )
    table.add_column(_("themes.col_theme"), min_width=14, no_wrap=True, ratio=2)
    table.add_column(_("themes.col_palette"), min_width=18, no_wrap=True, ratio=2)
    table.add_column(_("themes.col_status"), justify="right", min_width=14, no_wrap=True, ratio=1)

    total_items = len(theme_names) + (2 if custom else 1)

    for i, name in enumerate(theme_names):
        colors = BUILTIN_THEMES[name]
        is_selected = i == selected
        is_current = name == current
        marker = "❯ " if is_selected else "  "

        if is_current and is_selected:
            name_style = "bold green"
        elif is_current:
            name_style = "green"
        elif is_selected:
            name_style = "bold white"
        else:
            name_style = ""

        name_cell = Text(marker + name, style=name_style)

        swatch = Text()
        for role in _SWATCH_ROLES:
            swatch.append("█", style=colors[role])
            swatch.append(" ", style="default")

        if is_current:
            status = Text("● " + _("themes.current") + (" " + _("themes.plus_custom") if custom else ""), style="green")
        else:
            status = Text("", style="dim")

        row_bg = "on grey15" if is_selected else ""
        table.add_row(name_cell, swatch, status, style=row_bg)

    # Доп. пункты: кастомизация + сброс
    custom_idx = len(theme_names)
    is_sel_custom = selected == custom_idx
    marker = "❯ " if is_sel_custom else "  "
    style = "bold white" if is_sel_custom else ""
    bg = "on grey15" if is_sel_custom else ""
    table.add_row(
        Text(marker + _("themes.customize"), style=style),
        Text(_("themes.customize_hint"), style="dim"),
        Text("", style="dim"),
        style=bg,
    )

    if custom:
        reset_idx = custom_idx + 1
        is_sel_reset = selected == reset_idx
        marker = "❯ " if is_sel_reset else "  "
        style = "bold white" if is_sel_reset else ""
        bg = "on grey15" if is_sel_reset else ""
        table.add_row(
            Text(marker + _("themes.reset"), style=style),
            Text(_("themes.reset_hint"), style="dim"),
            Text("", style="dim"),
            style=bg,
        )

    panel = Panel(
        table,
        title=_("themes.title"),
        title_align="left",
        subtitle=f"{selected + 1}/{total_items}",
        subtitle_align="right",
        border_style="dim",
        padding=(0, 1),
    )

    buf = StringIO()
    render_console = Console(file=buf, highlight=False, force_terminal=True,
                             width=width, color_system="truecolor")
    render_console.print(panel)
    return buf.getvalue()


def themes_interactive():
    """Интерактивное меню с live-превью."""
    while True:
        current = get_active_theme_name()
        custom = has_custom_overrides()
        theme_names = list_themes()
        total = len(theme_names) + (2 if custom else 1)

        initial = 0
        for i, name in enumerate(theme_names):
            if name == current:
                initial = i
                break

        term_w = shutil.get_terminal_size((100, 24)).columns
        preview_w = min(76, term_w - 6)
        list_w = min(term_w, preview_w + 4)

        def render_fn(sel: int) -> str:
            # Палитра для превью: подсвеченная тема либо «текущая + custom»
            if sel < len(theme_names):
                colors = BUILTIN_THEMES[theme_names[sel]]
            else:
                # На пунктах кастом/сброс — показываем актуальную палитру.
                colors = get_theme()

            list_panel = _build_theme_list_panel(
                theme_names, sel, current, custom, list_w
            )
            preview = render_theme_preview(colors, width=preview_w)
            return list_panel + preview

        choice = _panel_menu_direct(
            render_fn,
            sys.stdout,
            _("themes.hint_apply"),
            total,
            initial,
        )

        if choice is None:
            return

        if custom and choice == len(theme_names) + 1:
            reset_custom()
            console.print(f"  [green]✓[/green] {_('themes.reset_done')}")
            continue

        if choice == len(theme_names):
            _theme_customize()
            continue

        chosen_name = theme_names[choice]
        if chosen_name == current and not custom:
            continue

        set_theme(chosen_name)
        console.print(f"  [green]✓[/green] {_('themes.applied', name=chosen_name)}")


def _build_customize_panel(roles_list: list[str], custom_overrides: dict,
                           current_colors: dict, selected: int, width: int) -> str:
    """Панель списка ролей для кастомизации."""
    table = Table(
        show_header=True,
        header_style="bold dim",
        border_style="dim",
        padding=(0, 1),
        show_edge=False,
        show_lines=False,
        expand=True,
    )
    table.add_column(_("themes.cust_col_role"), min_width=20, ratio=3)
    table.add_column(_("themes.cust_col_color"), min_width=12, no_wrap=True, ratio=1)
    table.add_column(_("themes.cust_col_preview"), min_width=10, no_wrap=True, ratio=1)

    for i, role in enumerate(roles_list):
        color = current_colors.get(role, "#ffffff")
        is_custom = role in custom_overrides
        is_selected = i == selected
        marker = "❯ " if is_selected else "  "
        custom_mark = "✎ " if is_custom else "  "

        style = "bold white" if is_selected else ""
        bg = "on grey15" if is_selected else ""

        label = ROLE_LABELS.get(role, role)
        name_cell = Text(marker + custom_mark + label, style=style)
        color_cell = Text(color, style=color)
        swatch = Text("████████", style=color)

        table.add_row(name_cell, color_cell, swatch, style=bg)

    back_idx = len(roles_list)
    is_sel_back = selected == back_idx
    marker = "❯ " if is_sel_back else "  "
    style = "bold white" if is_sel_back else ""
    bg = "on grey15" if is_sel_back else ""
    table.add_row(
        Text(marker + _("common.back"), style=style),
        Text(""), Text(""),
        style=bg,
    )

    panel = Panel(
        table,
        title=_("themes.cust_title"),
        title_align="left",
        subtitle=_("themes.cust_subtitle"),
        subtitle_align="right",
        border_style="dim",
        padding=(0, 1),
    )

    buf = StringIO()
    render_console = Console(file=buf, highlight=False, force_terminal=True,
                             width=width, color_system="truecolor")
    render_console.print(panel)
    return buf.getvalue()


def _theme_customize():
    """Меню кастомизации с live-превью."""
    hex_re = re.compile(r'^#[0-9a-fA-F]{6}$')

    while True:
        current_colors = get_theme()
        custom_overrides = config.get("theme_custom", {})
        if not isinstance(custom_overrides, dict):
            custom_overrides = {}

        roles_list = list(ROLES)
        total = len(roles_list) + 1

        term_w = shutil.get_terminal_size((100, 24)).columns
        preview_w = min(76, term_w - 6)
        list_w = min(term_w, preview_w + 4)

        def render_fn(sel: int) -> str:
            list_panel = _build_customize_panel(
                roles_list, custom_overrides, current_colors, sel, list_w
            )
            preview = render_theme_preview(current_colors, width=preview_w)
            return list_panel + preview

        choice = _panel_menu_direct(
            render_fn,
            sys.stdout,
            _("themes.hint_edit"),
            total,
            0,
        )

        if choice is None or choice == len(roles_list):
            return

        role = roles_list[choice]
        role_label = ROLE_LABELS.get(role, role)
        current_val = current_colors.get(role, "#ffffff")

        console.print()
        console.print(f"  [{current_val}]████[/{current_val}] [bold]{role_label}[/bold]: {current_val}")
        try:
            new_val = console.input(f"  [bold]{_('themes.cust_new_color')}:[/bold] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            continue

        if not new_val:
            continue

        if not hex_re.match(new_val):
            console.print(f"  [red]{_('themes.cust_invalid')}[/red]")
            continue

        set_custom_color(role, new_val)
        console.print(f"  [green]✓[/green] {role_label}: [{new_val}]████[/{new_val}] {new_val}")
        continue