"""Меню /permissions: решения по инструментам и массовые действия.

Строка инструмента — иконка решения, имя, область действия. Разделитель между
списком и массовыми действиями теперь заголовок секции, а не полоска из
псевдографики: вертикальных и горизонтальных линий внутри виджета быть не
должно, и на такой «пункт» больше нельзя встать курсором.

Выбор решения закрывает карточку инструмента: это терминальное действие —
разрешение выдано, возвращаемся к списку.
"""

from commands.menus._style import card_menu, confirm_delete, facts_line
from config.i18n import t as _
from config.permissions import (
    get_decision,
    get_scope,
    reset_all,
    reset_tool,
    set_decision,
)
from tools.registry import list_tools

_DECISION_ICON = {"ask": "·", "allow": "✓", "deny": "✗"}
_DECISION_STYLE = {"ask": "dim", "allow": "success", "deny": "error"}


def _scope_hint(scope: str) -> str:
    return {
        "session": _("perms.scope.session"),
        "process": _("perms.scope.process"),
        "forever": _("perms.scope.forever"),
    }.get(scope, scope)


def _tool_item(tool: str) -> dict:
    dec = get_decision(tool)
    scope = get_scope(tool)
    return {
        "icon": _DECISION_ICON.get(dec, "·"),
        "icon_style": _DECISION_STYLE.get(dec, "dim"),
        "label": tool,
        "hint": _scope_hint(scope) if scope else "",
        "badge": dec,
        "badge_style": _DECISION_STYLE.get(dec, "dim"),
    }


async def permissions_interactive():
    while True:
        tools_list = [tool for tool in list_tools() if tool != "poll"]
        items = [_tool_item(tool) for tool in tools_list]
        items.append({"label": _("perms.allow_all_title"), "skip": True})
        items.append({"label": _("perms.allow_all"), "hint": _("perms.allow_all_hint")})
        items.append({"label": _("perms.reset_all"), "hint": _("perms.reset_all_hint")})

        allow_idx = len(items) - 2
        reset_idx = len(items) - 1

        counts = {"allow": 0, "deny": 0, "ask": 0}
        for tool in tools_list:
            counts[get_decision(tool)] = counts.get(get_decision(tool), 0) + 1

        choice = await card_menu(
            items,
            title=_("perms.title"),
            facts=[facts_line(f"{counts['allow']} allow", f"{counts['deny']} deny",
                              f"{counts['ask']} ask", f"{len(tools_list)} tools")],
        )
        if choice is None:
            return

        if choice == allow_idx:
            scope_items = [
                {"label": _("perms.scope_session"), "hint": _("perms.scope_session_hint")},
                {"label": _("perms.scope_process"), "hint": _("perms.scope_process_hint")},
                {"label": _("perms.scope_forever"), "hint": _("perms.scope_forever_hint")},
                {"label": _("perms.cancel")},
            ]
            c = await card_menu(scope_items, title=_("perms.allow_all_title"))
            scope_map = {0: "session", 1: "process", 2: "forever"}
            if c in scope_map:
                scope = scope_map[c]
                for tool in tools_list:
                    set_decision(tool, "allow", scope)
            continue

        if choice == reset_idx:
            if await confirm_delete(_("perms.reset_q")):
                reset_all()
            continue

        if choice < len(tools_list):
            await _tool_detail_menu(tools_list[choice])


async def _tool_detail_menu(tool: str):
    """Карточка инструмента. Любое решение — терминальное, меню закрывается."""
    dec = get_decision(tool)
    scope = get_scope(tool)

    items = [
        {"label": _("perms.allow_session_long"), "hint": _("perms.allow_session_hint_short"),
         "icon": "✓", "icon_style": "success"},
        {"label": _("perms.allow_process_long"), "hint": _("perms.allow_process_hint_short"),
         "icon": "✓", "icon_style": "success"},
        {"label": _("perms.allow_forever_long"), "hint": _("perms.allow_forever_hint_short"),
         "icon": "✓", "icon_style": "success"},
        {"label": _("perms.deny_session_long"), "icon": "✗", "icon_style": "error"},
        {"label": _("perms.deny_forever_long"), "icon": "✗", "icon_style": "error"},
        {"label": _("perms.reset_one"), "hint": _("perms.reset_one_hint"),
         "icon": "·", "icon_style": "dim"},
        {"label": _("common.back")},
    ]
    c = await card_menu(
        items,
        title=tool,
        status=dec,
        status_style=_DECISION_STYLE.get(dec, "dim"),
        facts=[facts_line(_("perms.detail_title", name=tool),
                          _scope_hint(scope) if scope else "")],
    )
    if c is None or c == 6:
        return
    if c == 0:
        set_decision(tool, "allow", "session")
    elif c == 1:
        set_decision(tool, "allow", "process")
    elif c == 2:
        set_decision(tool, "allow", "forever")
    elif c == 3:
        set_decision(tool, "deny", "session")
    elif c == 4:
        set_decision(tool, "deny", "forever")
    elif c == 5:
        reset_tool(tool)
