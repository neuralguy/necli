import sys

from rich.console import Console
from rich.markup import escape

from config.lsp import (
    list_servers, set_enabled, remove_server,
    get_auto_diagnostics, set_auto_diagnostics,
)
from config.i18n import t as _
from ui.menu import select_menu

console = Console()


def _fmt_rss(kb: int | None) -> str:
    if not kb:
        return ""
    if kb < 1024:
        return f"{kb} KB"
    return f"{kb / 1024:.0f} MB"


def lsp_interactive():
    while True:
        servers = list_servers()
        from apis.lsp_client import LSPManager
        live = {info["id"]: info for info in LSPManager.instance().list_servers_info()}

        auto_diag = get_auto_diagnostics()
        items = []
        for cfg in servers:
            sid = cfg.get("id", "?")
            enabled = cfg.get("enabled", True)
            # Live-инфа может быть по нескольким key (cfg.id может породить несколько server.id вида "pyright@/path").
            live_for_cfg = [v for k, v in live.items() if k.startswith(f"{sid}@")]
            connected = [v for v in live_for_cfg if v.get("status") == "connected"]
            errored = [v for v in live_for_cfg if v.get("status") == "error"]
            if not enabled:
                icon = "○"
                status_str = _("lsp.status_off")
            elif connected:
                total_rss = sum((v.get("rss_kb") or 0) for v in connected)
                rss_str = _fmt_rss(total_rss) if total_rss else ""
                status_str = (_("lsp.status_running", n=len(connected)) + (" · " + rss_str if rss_str else "")).strip(" ·")
                icon = "●"
            elif errored:
                icon = "✗"
                status_str = _("lsp.status_error", msg=errored[0].get('error', '')[:40])
            else:
                icon = "·"
                status_str = _("lsp.status_lazy")
            cmd = cfg.get("command", "")
            exts = " ".join(cfg.get("extensions", []))
            items.append({
                "label": f"{icon} {sid}",
                "hint": f"{cmd} · {exts} · {status_str}",
            })
        items.append({
            "label": f"{'☑' if auto_diag else '☐'} {_('lsp.auto_diag')}",
            "hint": _("lsp.auto_diag_hint"),
        })
        items.append({"label": _("lsp.stop_all"), "hint": _("lsp.stop_all_hint")})

        if not servers:
            console.print()
            console.print(f"  [dim]{_('lsp.no_servers')}[/dim]")

        choice = select_menu(items, title=_("lsp.title"))
        if choice is None:
            return
        if choice == len(servers):
            set_auto_diagnostics(not auto_diag)
            continue
        if choice == len(servers) + 1:
            _stop_all()
            continue

        sid = servers[choice].get("id")
        if sid is None:
            continue
        _detail(sid)


def _detail(sid: str):
    while True:
        cfgs = list_servers()
        cfg = next((c for c in cfgs if c.get("id") == sid), None)
        if not cfg:
            return
        from apis.lsp_client import LSPManager
        live = LSPManager.instance().list_servers_info()
        live_for_cfg = [v for v in live if v["id"].startswith(f"{sid}@")]

        sys.stdout.write("\x1b7")
        sys.stdout.flush()

        enabled = cfg.get("enabled", True)
        cmd = cfg.get("command", "")
        args = cfg.get("args", [])
        exts = cfg.get("extensions", [])

        console.print()
        console.print(f"  [bold yellow]{escape(sid)}[/bold yellow]  [dim]({_('lsp.status_on') if enabled else _('lsp.status_off')})[/dim]")
        console.print(f"  [dim]{_('lsp.label_command')}[/dim] {escape(cmd)} {escape(' '.join(args))}")
        console.print(f"  [dim]{_('lsp.label_extensions')}[/dim] {escape(', '.join(exts))}")
        if live_for_cfg:
            for v in live_for_cfg:
                rss = _fmt_rss(v.get("rss_kb"))
                pid = v.get("pid")
                console.print(f"  [dim]·[/dim] {escape(v['id'])}  [dim]pid={pid} rss={rss} status={v['status']}[/dim]")
                if v.get("error"):
                    console.print(f"    [red]{_('mcp.error_label')}[/red] {escape(v['error'])}")
        else:
            console.print(f"  [dim]{_('lsp.not_running_lazy')}[/dim]")
        console.print()

        actions = [
            {"label": _("mcp.enable") if not enabled else _("mcp.disable"),
             "hint": _("lsp.enable_hint")},
            {"label": _("lsp.restart"), "hint": _("lsp.restart_hint")},
            {"label": _("api.delete"), "hint": _("lsp.delete_from_config")},
            {"label": _("common.back")},
        ]
        choice = select_menu(actions)

        sys.stdout.write("\x1b8")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

        if choice is None or choice == 3:
            return

        if choice == 0:
            set_enabled(sid, not enabled)
            if enabled:
                # был включён → теперь выключаем → останавливаем процесс(ы)
                _stop_for_sid(sid)
            else:
                _reload_configs()
            continue
        if choice == 1:
            _stop_for_sid(sid)
            _reload_configs()
            console.print(f"  [green]✓[/green] {_('lsp.server_restarted', name=sid)}")
            continue
        if choice == 2:
            confirm = select_menu(
                [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}],
                title=_("lsp.delete_q", name=sid),
            )
            if confirm == 0:
                _stop_for_sid(sid)
                remove_server(sid)
                _reload_configs()
                console.print(f"  [green]✓[/green] {_('lsp.server_removed', name=sid)}")
                return


def _stop_for_sid(sid: str):
    from apis.lsp_client import LSPManager
    mgr = LSPManager.instance()
    for key in list(mgr.servers.keys()):
        if key.startswith(f"{sid}@"):
            mgr.disconnect_by_key(key)


def _stop_all():
    from apis.lsp_client import LSPManager
    mgr = LSPManager.instance()
    for key in list(mgr.servers.keys()):
        mgr.disconnect_by_key(key)
    _reload_configs()
    console.print(f"  [green]✓[/green] {_('lsp.all_stopped')}")


def _reload_configs():
    """Перечитывает конфиги с диска и применяет к manager (для enabled/disabled)."""
    from apis.lsp_client import LSPManager
    from config.lsp import list_servers
    LSPManager.instance().init_from_configs(list_servers())