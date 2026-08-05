import inspect
import os
from dataclasses import dataclass

from rich.console import Console

import config
import session.storage as storage
from config.i18n import t as _
from config.themes import t
from logger import logger
from session import Session
from tools._paths import get_working_dir
from ui.menu import select_session_menu

console = Console()


@dataclass
class SlashResult:
    """Result of slash-command handling."""
    do_new: bool = False
    do_branch: bool = False
    do_reflect: bool = False
    switch_session: str | None = None
    change_dir: str | None = None
    do_compress: bool = False
    do_commit: bool = False
    commit_hint: str = ""
    switch_api: str | None = None
    switch_api_model: str | None = None
    toggle_think: bool = False
    toggle_tool_format: bool = False
    tg_toggle: bool | None = None


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


def _resolve_cd_target(raw: str) -> str | None:
    """Нормализует путь для /cd. None — такого каталога нет.

    Вынесено из `_handle_slash`: тот стал корутиной, а блокирующие обращения к
    ФС в корутине — отдельная синхронная функция (и линтер, и смысл).
    """
    target = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(target):
        target = os.path.join(get_working_dir(), target)
    target = os.path.realpath(target)
    return target if os.path.isdir(target) else None


async def _call_menu(fn, *args, **kwargs):
    """Зовёт точку входа меню, не зная, синхронная она или уже async.

    Меню переезжают на оверлеи Shell (им нужен `await overlays.*`) файл за
    файлом. Пока часть точек входа синхронная, ждём только то, что
    действительно корутина: иначе интеграция ломалась бы на каждом
    полупереехавшем меню, а `await` у синхронной функции — TypeError.
    """
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _handle_slash(
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

    if head == "/reflect":
        r.do_reflect = True
        return r

    if head == "/compress":
        if session.message_count == 0:
            return r
        r.do_compress = True
        return r

    if head == "/models":
        active_api = config.get_active_api()
        if not active_api:
            return r
        from apis.registry import get_definitions
        from apis.config import get_provider_balance
        defns = get_definitions()
        # Активный провайдер идёт первым, затем остальные включённые.
        ordered_ids = [active_api] + [
            pid for pid in defns if pid != active_api and defns[pid].enabled
        ]
        api_models = []
        model_providers = []   # provider_id, параллельно api_models
        group_labels = []      # имя провайдера для секции, параллельно api_models
        for pid in ordered_ids:
            defn = defns.get(pid)
            if not defn or not defn.models:
                continue
            balance = get_provider_balance(pid)
            label = f"{defn.name} · {balance:g}$" if balance else defn.name
            for m in defn.models:
                api_models.append(m)
                model_providers.append(pid)
                group_labels.append(label)
        if not api_models:
            return r
        current_api_model = config.get_active_api_model()
        from ui.menu import select_api_model_menu
        choice = await _call_menu(
            select_api_model_menu,
            api_models,
            current_id=current_api_model,
            group_labels=group_labels,
        )
        if choice is not None:
            chosen_model = api_models[choice]
            chosen_provider = model_providers[choice]
            if chosen_provider != active_api or chosen_model.id != current_api_model:
                if chosen_provider != active_api:
                    config.set_active_api(chosen_provider)
                config.set_active_api_model(chosen_model.id)
                r.switch_api = chosen_provider
                r.switch_api_model = chosen_model.id
        return r

    if head == "/sessions":
        sessions_list = storage.list_sessions(limit=0)
        if not sessions_list:
            return r

        choice = await _call_menu(select_session_menu, sessions_list,
                                  current_id=session.id)
        if choice is not None:
            sid = sessions_list[choice]["id"]
            if sid != session.id:
                r.switch_session = sid
        return r

    if head == "/stats":
        # [N] сохраняет прежний смысл — период общего свода в днях; теперь он
        # ещё и открывает вид сразу на разделе history с этим периодом.
        period_days = int(rest) if rest.strip().isdigit() else None
        from commands.menus.stats import stats_interactive
        await _call_menu(stats_interactive, session, period_days)
        return r

    if head == "/insights":
        from commands.menus.insights import insights_interactive
        await _call_menu(insights_interactive)
        return r

    if head == "/copy":
        n = int(rest) if rest.strip().isdigit() else 1
        if n < 1:
            n = 1
        assistant_msgs = [m for m in session.messages if m.role == "assistant"]
        if not assistant_msgs:
            return r
        picked = assistant_msgs[-n:]
        if len(picked) == 1:
            payload = picked[0].content or ""
        else:
            payload = "\n\n---\n\n".join((m.content or "") for m in picked)
        from ui.clipboard_copy import copy_to_clipboard
        copy_to_clipboard(payload)
        return r

    if head == "/history":
        from commands.menus.history import show_history
        n = int(rest) if rest.strip().isdigit() else 10
        await _call_menu(show_history, session, n)
        return r

    if head == "/cd":
        target = rest.strip()
        if not target:
            return r
        target = _resolve_cd_target(target)
        if target is None:
            return r
        r.change_dir = target
        return r

    if head == "/skills":
        from commands.menus.skills import skills_interactive
        await _call_menu(skills_interactive)
        return r

    if head == "/agents":
        from commands.menus.agents import agents_interactive
        await _call_menu(agents_interactive)
        return r

    if head == "/permissions":
        from commands.menus.permissions import permissions_interactive
        await _call_menu(permissions_interactive)
        return r

    if head == "/help":
        import sys
        if sys.stdin.isatty() and sys.stderr.isatty():
            from commands.menus.help import help_interactive
            await _call_menu(help_interactive)
        else:
            _print_help()
        return r

    if head == "/themes":
        from commands.menus.themes import themes_interactive
        await _call_menu(themes_interactive)
        return r

    if head == "/api":
        from commands.menus.api import api_interactive
        return await _call_menu(api_interactive)

    if head == "/tg":
        from commands.menus.telegram import telegram_interactive
        r.tg_toggle = await _call_menu(telegram_interactive)
        return r

    if head == "/mcp":
        from commands.menus.mcp import mcp_interactive
        await _call_menu(mcp_interactive)
        return r

    if head == "/lsp":
        from commands.menus.lsp import lsp_interactive
        await _call_menu(lsp_interactive)
        return r

    if head == "/params":
        from commands.menus.params import params_interactive
        await _call_menu(params_interactive)
        return r

    if head == "/autoprune":
        from commands.menus.autoprune import autoprune_interactive
        await _call_menu(autoprune_interactive)
        return r

    if head == "/proxy":
        arg = rest.strip()
        if arg:
            # Инлайн-режим: /proxy <url> | /proxy off|none|clear
            from apis.agent_adapter import invalidate_api_llm
            from commands.menus.proxy import _validate
            if arg.lower() in ("off", "none", "clear", "-"):
                config.set_value("proxy", "")
                invalidate_api_llm()
            elif _validate(arg):
                config.set_value("proxy", arg)
                invalidate_api_llm()
        else:
            from commands.menus.proxy import proxy_interactive
            await _call_menu(proxy_interactive)
        return r

    if head == "/lang":
        from commands.menus.lang import lang_interactive
        await _call_menu(lang_interactive)
        return r

    return r
