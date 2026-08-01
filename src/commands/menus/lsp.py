"""Меню /lsp: языковые серверы, их состояние и карточка сервера.

Карточка (команда, расширения, живые процессы) — часть виджета, а не запись в
scrollback: раньше она печаталась туда заново после каждого нажатия.
"""

from commands.menus._style import card_menu, confirm_delete, facts_line
from config.i18n import t as _
from config.lsp import (
    get_auto_diagnostics,
    list_servers,
    remove_server,
    set_auto_diagnostics,
    set_enabled,
)


def _fmt_rss(kb: int | None) -> str:
    if not kb:
        return ""
    if kb < 1024:
        return f"{kb} KB"
    return f"{kb / 1024:.0f} MB"


def _status_of(cfg: dict, live_for_cfg: list) -> tuple[str, str, str]:
    """(глиф, роль цвета, текст статуса) для строки списка."""
    if not cfg.get("enabled", True):
        return "○", "muted", _("lsp.status_off")
    connected = [v for v in live_for_cfg if v.get("status") == "connected"]
    errored = [v for v in live_for_cfg if v.get("status") == "error"]
    if connected:
        rss = _fmt_rss(sum((v.get("rss_kb") or 0) for v in connected))
        text = _("lsp.status_running", n=len(connected))
        return "●", "success", facts_line(text, rss)
    if errored:
        return "✗", "error", _("lsp.status_error", msg=errored[0].get("error", "")[:40])
    return "·", "dim", _("lsp.status_lazy")


async def lsp_interactive():
    while True:
        servers = list_servers()
        from apis.lsp_client import LSPManager
        live = LSPManager.instance().list_servers_info()

        auto_diag = get_auto_diagnostics()
        items = []
        for cfg in servers:
            sid = cfg.get("id", "?")
            # Один cfg.id может породить несколько server.id вида "pyright@/path".
            mine = [v for v in live if v["id"].startswith(f"{sid}@")]
            icon, role, status_str = _status_of(cfg, mine)
            items.append({
                "icon": icon,
                "icon_style": role,
                "label": sid,
                "hint": facts_line(cfg.get("command", ""),
                                   " ".join(cfg.get("extensions", []))),
                "badge": status_str,
                "badge_style": role,
            })
        items.append({
            "icon": "☑" if auto_diag else "☐",
            "icon_style": "success" if auto_diag else "dim",
            "label": _("lsp.auto_diag"),
            "hint": _("lsp.auto_diag_hint"),
        })
        items.append({"icon": " ", "label": _("lsp.stop_all"),
                      "hint": _("lsp.stop_all_hint")})

        facts = [f"{len(servers)} server(s)"] if servers else [_("lsp.no_servers")]
        choice = await card_menu(items, title=_("lsp.title"), facts=facts)
        if choice is None:
            return
        if choice == len(servers):
            set_auto_diagnostics(not auto_diag)
            continue
        if choice == len(servers) + 1:
            _stop_all()
            continue

        sid = servers[choice].get("id")
        if sid is not None:
            await _detail(sid)


async def _detail(sid: str):
    while True:
        cfg = next((c for c in list_servers() if c.get("id") == sid), None)
        if not cfg:
            return
        from apis.lsp_client import LSPManager
        live_for_cfg = [v for v in LSPManager.instance().list_servers_info()
                        if v["id"].startswith(f"{sid}@")]

        enabled = cfg.get("enabled", True)
        facts = [
            facts_line(f"{_('lsp.label_command')} {cfg.get('command', '')}",
                       " ".join(cfg.get("args", []))),
            f"{_('lsp.label_extensions')} {', '.join(cfg.get('extensions', []))}",
        ]
        if live_for_cfg:
            facts.extend(facts_line(
                v["id"], f"pid={v.get('pid')}",
                f"rss={_fmt_rss(v.get('rss_kb'))}", f"status={v['status']}",
                v.get("error") or "",
            ) for v in live_for_cfg)
        else:
            facts.append(_("lsp.not_running_lazy"))

        actions = [
            {"label": _("mcp.enable") if not enabled else _("mcp.disable"),
             "hint": _("lsp.enable_hint"),
             "icon": "●" if not enabled else "○",
             "icon_style": "success" if not enabled else "warning"},
            {"label": _("lsp.restart"), "hint": _("lsp.restart_hint"), "icon": "↻",
             "icon_style": "accent"},
            {"label": _("api.delete"), "hint": _("lsp.delete_from_config"), "icon": "✗",
             "icon_style": "error"},
            {"label": _("common.back"), "icon": " "},
        ]
        choice = await card_menu(
            actions, title=sid,
            status=_("lsp.status_on") if enabled else _("lsp.status_off"),
            status_style="success" if enabled else "muted",
            facts=facts,
        )

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
            continue
        if choice == 2 and await confirm_delete(_("lsp.delete_q", name=sid)):
            _stop_for_sid(sid)
            remove_server(sid)
            _reload_configs()
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


def _reload_configs():
    """Перечитывает конфиги с диска и применяет к manager (для enabled/disabled)."""
    from apis.lsp_client import LSPManager
    from config.lsp import list_servers as _list
    LSPManager.instance().init_from_configs(_list())
