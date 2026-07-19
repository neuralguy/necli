from rich.console import Console

from config.i18n import t as _
from config.permissions import (
    get_decision,
    get_scope,
    reset_all,
    reset_tool,
    set_decision,
)
from config.themes import t
from tools.registry import list_tools
from ui.menu import select_menu

console = Console()


def _hex_to_ansi_fg(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return ""
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"\x1b[38;2;{r};{g};{b}m"


_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"

_DECISION_ICON = {
    "ask":   "·",
    "allow": "✓",
    "deny":  "✗",
}

def _scope_hint(scope: str) -> str:
    return {
        "session": _("perms.scope.session"),
        "process": _("perms.scope.process"),
        "forever": _("perms.scope.forever"),
    }.get(scope, scope)


def _decision_color(dec: str) -> str:
    if dec == "allow":
        return _hex_to_ansi_fg(t("success"))
    if dec == "deny":
        return _hex_to_ansi_fg(t("error"))
    return _DIM


def _format_row(tool: str) -> dict:
    dec = get_decision(tool)
    scope = get_scope(tool)
    icon = _DECISION_ICON.get(dec, "·")
    color = _decision_color(dec)
    label = f"{color}{icon}{_RESET}  {tool}"
    hint_parts = [dec]
    if scope:
        hint_parts.append(_scope_hint(scope))
    hint = " · ".join(hint_parts)
    return {"label": label, "hint": hint}


def permissions_interactive():
    while True:
        tools_list = [tool for tool in list_tools() if tool != "poll"]
        items = [_format_row(tool) for tool in tools_list]
        items.append({"label": "─" * 30, "hint": ""})
        items.append({"label": _("perms.allow_all"), "hint": _("perms.allow_all_hint")})
        items.append({"label": _("perms.reset_all"), "hint": _("perms.reset_all_hint")})

        sep_idx = len(items) - 3
        allow_idx = len(items) - 2
        reset_idx = len(items) - 1

        choice = select_menu(items, title=_("perms.title"))
        if choice is None:
            return

        if choice == sep_idx:
            continue

        if choice == allow_idx:
            scope_items = [
                {"label": _("perms.scope_session"), "hint": _("perms.scope_session_hint")},
                {"label": _("perms.scope_process"), "hint": _("perms.scope_process_hint")},
                {"label": _("perms.scope_forever"), "hint": _("perms.scope_forever_hint")},
                {"label": _("perms.cancel")},
            ]
            c = select_menu(scope_items, title=_("perms.allow_all_title"))
            scope_map = {0: "session", 1: "process", 2: "forever"}
            if c in scope_map:
                scope = scope_map[c]
                for tool in tools_list:
                    set_decision(tool, "allow", scope)
                console.print(f"  [{t('success')}]{_('perms.all_allowed', scope=_scope_hint(scope))}[/{t('success')}]")
            continue

        if choice == reset_idx:
            confirm = [{"label": _("perms.yes_reset")}, {"label": _("common.cancel")}]
            c = select_menu(confirm, title=_("perms.reset_q"))
            if c == 0:
                reset_all()
                console.print(f"  [{t('warning')}]{_('perms.all_reset')}[/{t('warning')}]")
            continue

        tool = tools_list[choice]
        _tool_detail_menu(tool)


def _tool_detail_menu(tool: str):
    while True:
        dec = get_decision(tool)
        scope = get_scope(tool)
        console.print()
        rich_color = {"ask": "dim", "allow": "green", "deny": "red"}.get(dec, "dim")
        scope_part = f" ({_scope_hint(scope)})" if scope else ""
        console.print(
            f"  [bold]{tool}[/bold]  [{rich_color}]{dec}[/{rich_color}]{scope_part}"
        )

        items = [
            {"label": _("perms.allow_session_long"), "hint": _("perms.allow_session_hint_short")},
            {"label": _("perms.allow_process_long"), "hint": _("perms.allow_process_hint_short")},
            {"label": _("perms.allow_forever_long"), "hint": _("perms.allow_forever_hint_short")},
            {"label": "─" * 30, "hint": ""},
            {"label": _("perms.deny_session_long"),  "hint": ""},
            {"label": _("perms.deny_forever_long"),  "hint": ""},
            {"label": "─" * 30, "hint": ""},
            {"label": _("perms.reset_one"), "hint": _("perms.reset_one_hint")},
            {"label": _("common.back")},
        ]
        c = select_menu(items, title=_("perms.detail_title", name=tool))
        if c is None or c == 8:
            return
        if c == 0:
            set_decision(tool, "allow", "session")
        elif c == 1:
            set_decision(tool, "allow", "process")
        elif c == 2:
            set_decision(tool, "allow", "forever")
        elif c == 4:
            set_decision(tool, "deny", "session")
        elif c == 5:
            set_decision(tool, "deny", "forever")
        elif c == 7:
            reset_tool(tool)
