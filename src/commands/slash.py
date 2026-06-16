import os
from dataclasses import dataclass
from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.table import Table

import config
import models as app_models
from logger import logger
from config.themes import t
from config.i18n import t as _
import session.storage as storage
from session import Session
from tools._paths import get_working_dir
from ui import format_tokens, format_cost
from ui.menu import select_session_menu

console = Console()


@dataclass
class SlashResult:
    """Result of slash-command handling."""
    handled: bool = True
    do_new: bool = False
    do_branch: bool = False
    do_reflect: bool = False
    switch_session: Optional[str] = None
    change_dir: Optional[str] = None
    do_compress: bool = False
    do_decompress: bool = False
    do_commit: bool = False
    commit_hint: str = ""
    undo_n: Optional[int] = None
    switch_api: Optional[str] = None
    switch_api_model: Optional[str] = None
    toggle_think: bool = False
    toggle_tool_format: bool = False
    tg_toggle: Optional[bool] = None


def _add_grouped_model_rows(table: Table, by_model: dict) -> None:
    models_sorted = sorted(
        by_model.keys(),
        key=lambda m: (app_models.model_group_order(app_models.model_group(m)), m),
    )
    prev_group = None
    for m in models_sorted:
        group = app_models.model_group(m)
        if group != prev_group:
            if prev_group is not None:
                table.add_section()
            table.add_row(f"[bold dim]{group.upper()}[/bold dim]", "", "", "", "", "", "")
            prev_group = group
        d = by_model[m]
        total_tok = d["input_tokens"] + d["output_tokens"]
        table.add_row(
            f"  {m}", str(d["sessions"]), str(d["messages"]),
            format_tokens(d["input_tokens"]),
            format_tokens(d["output_tokens"]),
            format_tokens(total_tok), format_cost(d["cost"]),
        )


def _print_stats(period_days: int | None = None) -> None:
    st = storage.get_statistics(days=period_days)
    if st["total_sessions"] == 0:
        console.print(f"  [dim]{_('stats.no_data')}[/dim]")
        return

    title = _("stats.overall")
    if period_days is not None:
        suffix = "" if period_days == 1 else "s"
        title = _("stats.last_n_days", n=period_days, s=suffix)

    table = Table(
        border_style="dim", padding=(0, 1), show_header=True,
        header_style="bold dim", title=title, title_style="bold",
    )
    table.add_column(_("stats.col_model"), style="yellow")
    table.add_column(_("stats.col_sessions"), justify="right")
    table.add_column(_("stats.col_msgs"), justify="right")
    table.add_column(_("stats.col_input"), justify="right", style="cyan")
    table.add_column(_("stats.col_output"), justify="right", style="green")
    table.add_column(_("stats.col_total_tok"), justify="right")
    table.add_column(_("stats.col_cost"), justify="right", style="bold")

    _add_grouped_model_rows(table, st["by_model"])

    total_tok = st["total_input_tokens"] + st["total_output_tokens"]
    table.add_section()
    table.add_row(
        f"[bold]{_('stats.total')}[/bold]",
        f"[bold]{st['total_sessions']}[/bold]",
        f"[bold]{st['total_messages']}[/bold]",
        f"[bold cyan]{format_tokens(st['total_input_tokens'])}[/bold cyan]",
        f"[bold green]{format_tokens(st['total_output_tokens'])}[/bold green]",
        f"[bold]{format_tokens(total_tok)}[/bold]",
        f"[bold]{format_cost(st['total_cost'])}[/bold]",
    )
    console.print(table)


def _print_help() -> None:
    from commands.registry import by_category

    groups = by_category()

    # Колоночная ширина — по самой длинной "name + args_hint" среди всех команд.
    max_label = 0
    for _cat, _key, cmds in groups:
        for c in cmds:
            label_len = len(c.name) + (1 + len(c.args_hint) if c.args_hint else 0)
            if label_len > max_label:
                max_label = label_len
    col_width = max_label + 4

    accent = t("accent")
    console.print()
    for _cat, cat_key, cmds in groups:
        if not cmds:
            continue
        console.print(f"  [bold dim]── {_(cat_key)} ──[/bold dim]")
        for c in cmds:
            label = f"{c.name} {c.args_hint}" if c.args_hint else c.name
            padding = " " * (col_width - len(label))
            desc = _(c.desc_key)
            aliases = ""
            if c.aliases:
                aliases = f" [dim](alias: {', '.join(c.aliases)})[/dim]"
            console.print(f"  [bold {accent}]{label}[/bold {accent}]{padding}{desc}{aliases}")
        console.print()


def _normalize_cmd(cmd: str) -> tuple[str, str]:
    """Возвращает (canonical_name, rest_args). Алиасы резолвятся через registry."""
    from commands.registry import lookup
    parts = cmd.split(None, 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    resolved = lookup(head)
    if resolved is not None:
        return resolved.name, rest
    return head, rest


def _handle_slash(
    cmd: str,
    model: str,
    session: Session,
    last_elapsed: float | None,
) -> SlashResult:
    logger.info("slash: {!r} (model={})", cmd[:80], model)
    r = SlashResult()

    head, rest = _normalize_cmd(cmd)

    if head == "/new":
        r.do_new = True
        return r

    if head == "/branch":
        if session.message_count == 0:
            console.print(f"  [dim]{_('slash.branch_empty')}[/dim]")
            return r
        r.do_branch = True
        return r

    if head == "/commit":
        r.do_commit = True
        r.commit_hint = rest.strip()
        return r

    if head == "/think":
        r.toggle_think = True
        return r

    if head == "/tool_format":
        r.toggle_tool_format = True
        return r

    if head == "/plan":
        from agent import get_current_ctx
        from planner import render_plan_panel
        ctx = get_current_ctx()
        plan = ctx.plan if ctx else None
        if plan is None or not plan.steps:
            console.print(f"  [dim]{_('sh.no_plan')}[/dim]")
            return r
        console.print()
        console.print(render_plan_panel(plan, compact=False))
        console.print()
        return r

    if head == "/reflect":
        r.do_reflect = True
        return r

    if head == "/compress":
        if session.message_count == 0:
            console.print(f"  [dim]{_('slash.nothing_to_compress')}[/dim]")
            return r
        r.do_compress = True
        return r

    if head == "/decompress":
        r.do_decompress = True
        return r

    if head == "/undo":
        try:
            r.undo_n = int(rest.strip())
        except ValueError:
            r.undo_n = 1
        if r.undo_n == 0:
            r.undo_n = 1
        return r

    if head == "/models":
        active_api = config.get_active_api()
        if not active_api:
            console.print(f"  [red]{_('slash.api_not_configured')}[/red]")
            return r
        from apis.registry import get_definition
        defn = get_definition(active_api)
        if not defn or not defn.models:
            console.print(f"  [dim]{_('slash.no_models_provider', name=active_api)}[/dim]")
            return r
        current_api_model = config.get_active_api_model()
        api_models = defn.models
        from ui.menu import select_api_model_menu
        choice = select_api_model_menu(api_models, current_id=current_api_model, provider_name=defn.name)
        if choice is not None:
            chosen_model = api_models[choice]
            if chosen_model.id != current_api_model:
                config.set_active_api_model(chosen_model.id)
                r.switch_api = active_api
                r.switch_api_model = chosen_model.id
                console.print(
                    f"  [green]\u2713[/green] \u2192 [yellow]{chosen_model.display_name}[/yellow]"
                    f" [dim]({chosen_model.id})[/dim]"
                )
        return r

    if head == "/sessions":
        sessions_list = storage.list_sessions(limit=0)
        if not sessions_list:
            console.print(f"  [dim]{_('slash.no_sessions')}[/dim]")
            return r

        choice = select_session_menu(sessions_list, current_id=session.id)
        if choice is not None:
            sid = sessions_list[choice]["id"]
            if sid != session.id:
                r.switch_session = sid
        return r

    if head == "/stats":
        period_days = int(rest) if rest.strip().isdigit() else None
        _print_stats(period_days)
        return r

    if head == "/insights":
        from commands.menus.insights import insights_interactive
        insights_interactive()
        return r

    if head == "/copy":
        n = int(rest) if rest.strip().isdigit() else 1
        if n < 1:
            n = 1
        assistant_msgs = [m for m in session.messages if m.role == "assistant"]
        if not assistant_msgs:
            console.print(f"  [dim]{_('sh.copy_empty')}[/dim]")
            return r
        picked = assistant_msgs[-n:]
        if len(picked) == 1:
            payload = picked[0].content or ""
        else:
            payload = "\n\n---\n\n".join((m.content or "") for m in picked)
        from ui.clipboard_copy import copy_to_clipboard
        err = copy_to_clipboard(payload)
        if err:
            console.print(f"  [red]{_('sh.copy_fail', err=err)}[/red]")
        else:
            console.print(f"  [green]✓[/green] [dim]{_('sh.copy_ok', n=len(picked), chars=len(payload))}[/dim]")
        return r

    if head == "/history":
        from commands.menus.history import show_history
        n = int(rest) if rest.strip().isdigit() else 10
        show_history(session, n)
        return r

    if head == "/cd":
        target = rest.strip()
        if not target:
            console.print(f"  [bold {t('success')}]{escape(os.getcwd())}[/bold {t('success')}]")
            return r
        target = os.path.expanduser(target)
        target = os.path.expandvars(target)
        if not os.path.isabs(target):
            target = os.path.join(get_working_dir(), target)
        target = os.path.realpath(target)
        if not os.path.isdir(target):
            console.print(f"  [red]{_('slash.not_a_directory', path=target)}[/red]")
            return r
        r.change_dir = target
        console.print(f"  [green]✓[/green] → [bold {t('success')}]{escape(target)}[/bold {t('success')}]")
        return r

    if head == "/ssh":
        from commands.menus.ssh import ssh_interactive
        ssh_interactive()
        return r

    if head == "/skills":
        from commands.menus.skills import skills_interactive
        skills_interactive()
        return r

    if head == "/agents":
        from commands.menus.agents import agents_interactive
        agents_interactive()
        return r

    if head == "/workflows":
        from commands.menus.workflows import workflows_interactive
        workflows_interactive(rest)
        return r

    if head == "/permissions":
        from commands.menus.permissions import permissions_interactive
        permissions_interactive()
        return r

    if head == "/help":
        _print_help()
        return r

    if head == "/themes":
        from commands.menus.themes import themes_interactive
        themes_interactive()
        return r

    if head == "/api":
        from commands.menus.api import api_interactive
        return api_interactive()

    if head == "/tg":
        from commands.menus.telegram import telegram_interactive
        r.tg_toggle = telegram_interactive()
        return r

    if head == "/mcp":
        from commands.menus.mcp import mcp_interactive
        mcp_interactive()
        return r

    if head == "/lsp":
        from commands.menus.lsp import lsp_interactive
        lsp_interactive()
        return r

    if head == "/params":
        from commands.menus.params import params_interactive
        params_interactive()
        return r

    if head == "/lang":
        from commands.menus.lang import lang_interactive
        lang_interactive()
        return r

    console.print(f"  [dim]{_('slash.unknown_hint')}[/dim]")
    return r