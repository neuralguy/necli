import sys

from rich.console import Console
from rich.markup import escape

from config.mcp import list_servers, add_server, remove_server, set_enabled, get_server
from config.i18n import t as _
from ui.menu import select_menu

console = Console()


def mcp_interactive():
    while True:
        servers = list_servers()
        from apis.mcp_client import MCPManager
        mgr_servers = {s["id"]: s for s in MCPManager.instance().list_servers_info()}

        items = []
        for cfg in servers:
            sid = cfg.get("id", "?")
            enabled = cfg.get("enabled", True)
            info = mgr_servers.get(sid, {})
            status = info.get("status", "disconnected")
            tool_count = info.get("tool_count", 0)
            if not enabled:
                icon = "○"
                status_str = _("mcp.status_off")
            elif status == "connected":
                icon = "●"
                status_str = _("mcp.status_tools", n=tool_count)
            elif status == "error":
                icon = "✗"
                status_str = _("mcp.status_error", msg=info.get('error', '')[:40])
            else:
                icon = "·"
                status_str = status
            cmd = cfg.get("command", "")
            args = " ".join(cfg.get("args", []))
            items.append({
                "label": f"{icon} {sid}",
                "hint": f"{cmd} {args} · {status_str}",
            })
        items.append({"label": _("mcp.add_server"), "hint": _("mcp.add_hint")})
        items.append({"label": _("mcp.reconnect_all"), "hint": ""})

        if not servers:
            console.print()
            console.print(f"  [dim]{_('mcp.no_servers')}[/dim]")
            console.print(f"  [dim]{_('mcp.examples')}[/dim]")

        choice = select_menu(items, title=_("mcp.title"))
        if choice is None:
            return
        if choice == len(servers):
            _add_interactive()
            continue
        if choice == len(servers) + 1:
            _reconnect_all()
            continue

        sid = servers[choice].get("id")
        action = _detail(sid)
        if action == "back":
            continue


def _detail(sid: str):
    while True:
        cfg = get_server(sid)
        if not cfg:
            return "back"
        from apis.mcp_client import MCPManager
        info_map = {s["id"]: s for s in MCPManager.instance().list_servers_info()}
        info = info_map.get(sid, {})

        sys.stdout.write("\x1b7")
        sys.stdout.flush()

        enabled = cfg.get("enabled", True)
        status = info.get("status", "disconnected") if enabled else "off"
        tools = info.get("tools", [])
        cmd = cfg.get("command", "")
        args = cfg.get("args", [])
        env = cfg.get("env") or {}

        console.print()
        console.print(f"  [bold yellow]{escape(sid)}[/bold yellow]  [dim]({status})[/dim]")
        console.print(f"  [dim]{_('mcp.command_label')}[/dim] {escape(cmd)} {escape(' '.join(args))}")
        if env:
            console.print(f"  [dim]{_('mcp.env_keys_label')}[/dim] {escape(', '.join(env.keys()))}")
        if info.get("error"):
            console.print(f"  [red]{_('mcp.error_label')}[/red] {escape(info['error'])}")
        if tools:
            console.print(f"  [dim]{_('mcp.tools_label')} ({len(tools)}):[/dim] {escape(', '.join(tools[:12]))}{' …' if len(tools) > 12 else ''}")
        console.print()

        actions = [
            {"label": _("mcp.reconnect"), "hint": _("mcp.reconnect_hint")},
            {"label": _("mcp.enable") if not enabled else _("mcp.disable")},
            {"label": _("api.delete"), "hint": _("api.delete_permanent")},
            {"label": _("common.back")},
        ]
        choice = select_menu(actions)

        sys.stdout.write("\x1b8")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

        if choice is None or choice == 3:
            return "back"

        if choice == 0:
            _reconnect_one(sid)
            continue
        if choice == 1:
            set_enabled(sid, not enabled)
            _reconnect_all(silent=True)
            continue
        if choice == 2:
            confirm = select_menu(
                [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}],
                title=_("mcp.delete_q", name=sid),
            )
            if confirm == 0:
                from apis.mcp_client import MCPManager
                from tools.registry import TOOL_REGISTRY
                MCPManager.instance().disconnect(sid)
                for k in list(TOOL_REGISTRY.keys()):
                    if k.startswith(f"mcp__{sid}__"):
                        TOOL_REGISTRY.pop(k, None)
                remove_server(sid)
                console.print(f"  [green]✓[/green] {_('mcp.server_removed', name=sid)}")
                return "back"


def _add_interactive():
    console.print()
    console.print(f"  [dim]{_('mcp.add_example')}[/dim]")
    try:
        sid = console.input(f"  [bold]{_('mcp.field_server_id')}:[/bold] ").strip()
        if not sid:
            return
        if get_server(sid):
            console.print(f"  [red]{_('mcp.already_exists', name=sid)}[/red]")
            return
        command = console.input(f"  [bold]{_('mcp.field_command')}:[/bold] ").strip()
        if not command:
            return
        args_raw = console.input(f"  [bold]{_('mcp.field_args')}[/bold] [dim]({_('mcp.field_args_hint')}):[/dim] ").strip()
        args = args_raw.split() if args_raw else []
        env_raw = console.input(f"  [bold]{_('mcp.field_env')}[/bold] [dim]({_('mcp.field_env_hint')}):[/dim] ").strip()
        env: dict[str, str] = {}
        for token in env_raw.split():
            if "=" in token:
                k, v = token.split("=", 1)
                env[k] = v
        cfg = {
            "id": sid,
            "command": command,
            "args": args,
            "env": env,
            "transport": "stdio",
            "enabled": True,
        }
        add_server(cfg)
        console.print(f"  [green]✓[/green] {_('mcp.added_connecting', name=sid)}")
        _reconnect_all(silent=True)
        from apis.mcp_client import MCPManager
        info = {s["id"]: s for s in MCPManager.instance().list_servers_info()}.get(sid, {})
        if info.get("status") == "connected":
            console.print(f"  [green]✓[/green] {_('mcp.connected', n=info.get('tool_count', 0))}")
        else:
            console.print(f"  [red]✗[/red] {info.get('error', _('mcp.failed_to_connect'))}")
    except (KeyboardInterrupt, EOFError):
        console.print()


def _reconnect_one(sid: str):
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
    with console.status(f"[cyan]{_('mcp.connecting_one', name=sid)}[/cyan]", spinner="dots"):
        srv = mgr.connect(cfg)
    if srv.status == "connected":
        _register_in_tool_registry()
        console.print(f"  [green]✓[/green] {_('mcp.connected_one', name=sid, n=len(srv.tools))}")
    else:
        console.print(f"  [red]✗[/red] {escape(sid)}: {escape(srv.error or '')}")


def _reconnect_all(silent: bool = False):
    from apis.mcp_client import reconnect_mcp
    if silent:
        try:
            reconnect_mcp()
        except Exception as e:
            console.print(f"  [red]✗[/red] {escape(str(e))}")
        return
    with console.status(f"[cyan]{_('mcp.reconnecting')}[/cyan]", spinner="dots"):
        try:
            n = reconnect_mcp()
        except Exception as e:
            console.print(f"  [red]✗[/red] {escape(str(e))}")
            return
    console.print(f"  [green]✓[/green] {_('mcp.servers_connected', n=n)}")