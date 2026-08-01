"""Меню /mcp: серверы MCP, их состояние и карточка сервера.

Спиннер подключения живёт в динамической зоне Shell (`with_spinner` из
`_style`), карточка сервера — внутри виджета, а события подключения,
добавления, удаления и ошибок — в динамическом notice.
"""

from commands.menus._style import (
    card_menu,
    confirm_delete,
    facts_line,
    with_spinner,
)
from config.i18n import t as _
from config.mcp import add_server, get_server, list_servers, remove_server, set_enabled
from ui import overlays


def _status_of(cfg: dict, info: dict) -> tuple[str, str, str]:
    """(глиф, роль цвета, текст статуса) для строки списка."""
    if not cfg.get("enabled", True):
        return "○", "muted", _("mcp.status_off")
    status = info.get("status", "disconnected")
    if status == "connected":
        return "●", "success", _("mcp.status_tools", n=info.get("tool_count", 0))
    if status == "error":
        return "✗", "error", _("mcp.status_error", msg=info.get("error", "")[:40])
    return "·", "dim", status


async def mcp_interactive():
    while True:
        servers = list_servers()
        from apis.mcp_client import MCPManager
        mgr_servers = {s["id"]: s for s in MCPManager.instance().list_servers_info()}

        items = []
        for cfg in servers:
            sid = cfg.get("id", "?")
            icon, role, status_str = _status_of(cfg, mgr_servers.get(sid, {}))
            args = " ".join(cfg.get("args", []))
            items.append({
                "icon": icon,
                "icon_style": role,
                "label": sid,
                "hint": f"{cfg.get('command', '')} {args}".strip(),
                "badge": status_str,
                "badge_style": role,
            })
        items.append({"label": _("mcp.add_server"), "hint": _("mcp.add_hint")})
        items.append({"label": _("mcp.reconnect_all"), "hint": ""})

        facts = [f"{len(servers)} server(s)"] if servers else [
            _("mcp.no_servers"), _("mcp.examples")]

        choice = await card_menu(items, title=_("mcp.title"), facts=facts)
        if choice is None:
            return
        if choice == len(servers):
            await _add_interactive()
            continue
        if choice == len(servers) + 1:
            await _reconnect_all()
            continue

        await _detail(servers[choice].get("id"))


async def _detail(sid: str):
    while True:
        cfg = get_server(sid)
        if not cfg:
            return "back"
        from apis.mcp_client import MCPManager
        info_map = {s["id"]: s for s in MCPManager.instance().list_servers_info()}
        info = info_map.get(sid, {})

        enabled = cfg.get("enabled", True)
        status = info.get("status", "disconnected") if enabled else "off"
        tools = info.get("tools", [])
        env = cfg.get("env") or {}

        facts = [facts_line(cfg.get("command", ""), " ".join(cfg.get("args", [])))]
        if env:
            facts.append(f"{_('mcp.env_keys_label')} {', '.join(env.keys())}")
        if info.get("error"):
            facts.append(f"{_('mcp.error_label')} {info['error']}")
        if tools:
            facts.append(f"{_('mcp.tools_label')} ({len(tools)}): "
                         f"{', '.join(tools[:12])}{' …' if len(tools) > 12 else ''}")

        actions = [
            {"label": _("mcp.reconnect"), "hint": _("mcp.reconnect_hint"), "icon": "↻",
             "icon_style": "accent"},
            {"label": _("mcp.enable") if not enabled else _("mcp.disable"),
             "icon": "●" if not enabled else "○",
             "icon_style": "success" if not enabled else "warning"},
            {"label": _("api.delete"), "hint": _("api.delete_permanent"), "icon": "✗",
             "icon_style": "error"},
            {"label": _("common.back"), "icon": " "},
        ]
        choice = await card_menu(
            actions, title=sid, status=status,
            status_style="success" if status == "connected" else "muted",
            facts=facts,
        )

        if choice is None or choice == 3:
            return "back"

        if choice == 0:
            await _reconnect_one(sid)
            continue
        if choice == 1:
            set_enabled(sid, not enabled)
            await _reconnect_all(silent=True)
            continue
        if choice == 2 and await confirm_delete(_("mcp.delete_q", name=sid)):
            from apis.mcp_client import MCPManager
            from tools.registry import TOOL_REGISTRY
            MCPManager.instance().disconnect(sid)
            for k in list(TOOL_REGISTRY.keys()):
                if k.startswith(f"mcp__{sid}__"):
                    TOOL_REGISTRY.pop(k, None)
            remove_server(sid)
            return "back"


async def _add_interactive():
    sid = await overlays.ask_text(f"{_('mcp.field_server_id')} ({_('mcp.add_example')}):")
    if not sid:
        return
    if get_server(sid):
        return
    command = await overlays.ask_text(f"{_('mcp.field_command')}:")
    if not command:
        return
    args_raw = await overlays.ask_text(f"{_('mcp.field_args')} ({_('mcp.field_args_hint')}):")
    if args_raw is None:
        return  # esc в любом поле отменяет добавление, как прежний Ctrl+C
    env_raw = await overlays.ask_text(f"{_('mcp.field_env')} ({_('mcp.field_env_hint')}):")
    if env_raw is None:
        return
    env: dict[str, str] = {}
    for token in env_raw.split():
        if "=" in token:
            k, v = token.split("=", 1)
            env[k] = v
    add_server({
        "id": sid,
        "command": command,
        "args": args_raw.split() if args_raw else [],
        "env": env,
        "transport": "stdio",
        "enabled": True,
    })
    await _reconnect_all(silent=True)


async def _reconnect_one(sid: str):
    from apis.mcp_client import MCPManager, _register_in_tool_registry
    from tools.registry import TOOL_REGISTRY
    mgr = MCPManager.instance()
    for k in list(TOOL_REGISTRY.keys()):
        if k.startswith(f"mcp__{sid}__"):
            TOOL_REGISTRY.pop(k, None)
    mgr.disconnect(sid)
    cfg = get_server(sid)
    if not cfg or not cfg.get("enabled", True):
        return
    srv = await with_spinner(_("mcp.connecting_one", name=sid), mgr.connect, cfg)
    if srv.status == "connected":
        _register_in_tool_registry()


async def _reconnect_all(silent: bool = False):
    import asyncio

    from apis.mcp_client import reconnect_mcp
    if silent:
        try:
            await asyncio.to_thread(reconnect_mcp)
        except Exception:
            pass
        return
    try:
        await with_spinner(_("mcp.reconnecting"), reconnect_mcp)
    except Exception:
        return
