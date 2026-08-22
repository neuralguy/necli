"""Меню /tools: включение и отключение доступных инструментов."""

from commands.menus._style import card_menu, facts_line
from config.i18n import t as _
from tools.registry import list_tool_info, set_tool_enabled


def _tool_item(tool: dict[str, object]) -> dict:
    enabled = bool(tool["enabled"])
    return {
        "icon": "●" if enabled else "○",
        "icon_style": "success" if enabled else "muted",
        "label": str(tool["name"]),
        "hint": str(tool["description"]),
        "badge": _("tools.enabled") if enabled else _("tools.disabled"),
        "badge_style": "success" if enabled else "muted",
    }


async def tools_interactive():
    while True:
        tools = list_tool_info()
        choice = await card_menu(
            [_tool_item(tool) for tool in tools],
            title=_("tools.title"),
            facts=[
                facts_line(
                    _("tools.hint"),
                    _(
                        "tools.count",
                        enabled=sum(bool(t["enabled"]) for t in tools),
                        total=len(tools),
                    ),
                )
            ],
        )
        if choice is None:
            return
        tool = tools[choice]
        enabled = bool(tool["enabled"])
        action = await card_menu(
            [
                {
                    "label": _("tools.disable") if enabled else _("tools.enable"),
                    "hint": _("tools.disable_hint")
                    if enabled
                    else _("tools.enable_hint"),
                    "icon": "○" if enabled else "●",
                    "icon_style": "warning" if enabled else "success",
                    "action": True,
                },
                {"label": _("common.back")},
            ],
            title=str(tool["name"]),
            status=_("tools.enabled") if enabled else _("tools.disabled"),
            status_style="success" if enabled else "muted",
            facts=[str(tool["description"])],
        )
        if action == 0:
            set_tool_enabled(str(tool["name"]), not enabled)
