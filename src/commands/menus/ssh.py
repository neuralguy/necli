import sys

from rich.console import Console
from config.ssh import list_hosts, add_host, remove_host, parse_host_string
from config.i18n import t as _
from tools.ssh import check_connection
from ui.menu import select_menu

console = Console()


def ssh_interactive():
    while True:
        hosts = list_hosts()

        items = []
        for alias, cfg in hosts.items():
            user = cfg.get('user', 'root')
            host = cfg.get('host', '?')
            port = cfg.get('port', 22)
            confirm = '🔒' if cfg.get('confirm_dangerous', True) else '🔓'
            items.append({
                "label": f"{confirm} {alias}",
                "hint": f"{user}@{host}:{port}",
            })
        items.append({"label": _("ssh.add_host"), "hint": ""})

        if not hosts:
            console.print(f"  [dim]{_('ssh.no_hosts')}[/dim]")

        choice = select_menu(items, title=_("ssh.title"))
        if choice is None:
            return
        if choice == len(hosts):
            _ssh_add_interactive()
            continue

        alias, cfg = next(item for i, item in enumerate(hosts.items()) if i == choice)
        action = _ssh_detail(alias, cfg)
        if action == "back":
            continue
        return


def _ssh_detail(alias: str, cfg: dict):
    while True:
        user = cfg.get('user', 'root')
        host = cfg.get('host', '?')
        port = cfg.get('port', 22)
        key = cfg.get('key', '')
        confirm = cfg.get('confirm_dangerous', True)
        confirm_str = f"[green]{_('ssh.yes')}[/green]" if confirm else f"[red]{_('ssh.no')}[/red]"
        key_str = key if key else _("ssh.key_system")

        sys.stdout.write("\x1b7")
        sys.stdout.flush()

        console.print()
        console.print(f"  [bold yellow]{alias}[/bold yellow]")
        console.print(f"  [dim]{user}@{host}:{port}[/dim]")
        console.print(f"  [dim]{_('ssh.key_label')} {key_str}[/dim]")
        console.print(f"  [dim]{_('ssh.confirm_dangerous_label')} {confirm_str}[/dim]")
        console.print()

        actions = [
            {"label": _("ssh.test_connection")},
            {"label": _("api.delete"), "hint": _("api.delete_permanent")},
            {"label": _("common.back")},
        ]
        choice = select_menu(actions)

        sys.stdout.write("\x1b8")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

        if choice is None or choice == 2:
            return "back"

        if choice == 0:
            with console.status(f"[cyan]{_('ssh.connecting', name=alias)}[/cyan]", spinner="dots"):
                ok, info = check_connection(alias)
            if ok:
                console.print(f"  [green]✓[/green] {_('ssh.conn_ok', info=info)}")
            else:
                console.print(f"  [red]✗[/red] {_('ssh.conn_err', info=info)}")
            continue

        if choice == 1:
            confirm_items = [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}]
            c = select_menu(confirm_items, title=_("ssh.delete_q", name=alias))
            if c == 0:
                remove_host(alias)
                console.print(f"  [green]✓[/green] {_('ssh.host_removed', name=alias)}")
                return "back"
            continue


def _ssh_add_interactive():
    console.print()
    try:
        alias = console.input(f"  [bold]{_('ssh.field_alias')}:[/bold] ").strip()
        if not alias:
            return
        host_str = console.input(f"  [bold]{_('ssh.field_userhost')}:[/bold] ").strip()
        if not host_str:
            return
        user, host, port = parse_host_string(host_str)
        add_host(alias, host, user=user, port=port)
        console.print(f"  [green]✓[/green] {_('ssh.host_added', name=alias, target=f'{user}@{host}:{port}')}")
    except (KeyboardInterrupt, EOFError):
        console.print()
