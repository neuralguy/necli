"""Меню /ssh: список хостов и карточка хоста.

Карточка хоста живёт внутри виджета; события показываются динамически.
"""

from commands.menus._style import card_menu, confirm_delete, facts_line
from config.i18n import t as _
from config.ssh import add_host, list_hosts, parse_host_string, remove_host
from ui import overlays


async def ssh_interactive():
    while True:
        hosts = list_hosts()

        items = []
        for alias, cfg in hosts.items():
            safe = cfg.get("confirm_dangerous", True)
            items.append({
                "icon": "🔒" if safe else "🔓",
                "icon_style": "success" if safe else "warning",
                "label": alias,
                "hint": f"{cfg.get('user', 'root')}@{cfg.get('host', '?')}"
                        f":{cfg.get('port', 22)}",
                "badge": cfg.get("key", "") or _("ssh.key_system"),
                "badge_style": "dim",
            })
        items.append({"icon": " ", "label": _("ssh.add_host"), "hint": ""})

        facts = [f"{len(hosts)} host(s)"] if hosts else [_("ssh.no_hosts")]
        choice = await card_menu(items, title=_("ssh.title"), facts=facts)
        if choice is None:
            return
        if choice == len(hosts):
            await _ssh_add_interactive()
            continue

        host_items = list(hosts.items())
        if not (0 <= choice < len(host_items)):
            continue
        alias, cfg = host_items[choice]
        action = await _ssh_detail(alias, cfg)
        if action == "back":
            continue
        return


async def _ssh_detail(alias: str, cfg: dict):
    while True:
        user = cfg.get("user", "root")
        host = cfg.get("host", "?")
        port = cfg.get("port", 22)
        confirm_dangerous = cfg.get("confirm_dangerous", True)

        actions = [
            {"label": _("ssh.test_connection"), "icon": "↻", "icon_style": "accent"},
            {"label": _("api.delete"), "hint": _("api.delete_permanent"), "icon": "✗",
             "icon_style": "error"},
            {"label": _("common.back"), "icon": " "},
        ]
        choice = await card_menu(
            actions, title=alias,
            status=f"{user}@{host}:{port}", status_style="accent",
            facts=[facts_line(
                f"{_('ssh.key_label')} {cfg.get('key', '') or _('ssh.key_system')}",
                f"{_('ssh.confirm_dangerous_label')} "
                f"{_('ssh.yes') if confirm_dangerous else _('ssh.no')}",
            )],
        )

        if choice is None or choice == 2:
            return "back"

        if choice == 0:
            continue

        if choice == 1:
            if await confirm_delete(_("ssh.delete_q", name=alias)):
                remove_host(alias)
                return "back"
            continue


async def _ssh_add_interactive():
    alias = await overlays.ask_text(f"{_('ssh.field_alias')}:")
    if not alias:
        return
    host_str = await overlays.ask_text(f"{_('ssh.field_userhost')}:")
    if not host_str:
        return
    user, host, port = parse_host_string(host_str)
    add_host(alias, host, user=user, port=port)
