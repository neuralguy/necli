"""Menu /telegram — Telegram bridge configuration."""

import asyncio

from rich.console import Console
from rich.markup import escape

import config
from config.i18n import t as _
from ui.menu import select_menu

console = Console()

def _ainput(prompt: str) -> str:
    """Sync input через rich.console."""
    try:
        return console.input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        console.print()
        return ""

def _mask_token(token: str) -> str:
    if not token:
        return "—"
    if len(token) <= 12:
        return token[:4] + "…"
    return token[:6] + "…" + token[-4:]

def telegram_interactive():
    """Интерактивное меню Telegram-моста."""
    while True:
        token = config.get_telegram_bot_token()
        chat_id = config.get_telegram_chat_id()
        enabled = config.get_telegram_enabled()

        from apis.telegram import get_bridge
        bridge = get_bridge()
        running = bridge.is_running

        status = f"[green]{_('tg.on')}[/green]" if enabled else f"[dim]{_('tg.off')}[/dim]"
        run_status = f"[green]{_('tg.bot_running')}[/green]" if running else f"[dim]{_('tg.bot_stopped')}[/dim]"

        console.print()
        console.print(f"  [bold]{_('tg.header')}[/bold]  {status}  ·  {run_status}")
        console.print(f"  [dim]{_('tg.token_label')} {escape(_mask_token(token))}[/dim]")
        console.print(f"  [dim]{_('tg.chat_id_label')} {escape(chat_id) if chat_id else '—'}[/dim]")
        console.print()

        items = [
            {"label": _("tg.set_token"), "hint": _("tg.set_token_hint")},
            {"label": _("tg.set_chat"), "hint": _("tg.set_chat_hint")},
            {"label": _("tg.discover"), "hint": _("tg.discover_hint")},
            {"label": _("tg.test_send"), "hint": _("tg.test_send_hint")},
            {
                "label": _("tg.disable") if enabled else _("tg.enable"),
                "hint": _("tg.enable_hint"),
            },
            {"label": _("common.back")},
        ]
        choice = select_menu(items, title=_("tg.title"))
        if choice is None or choice == 5:
            return

        if choice == 0:
            new_token = _ainput(f"  [bold]{_('tg.field_token')}:[/bold] ")
            if new_token:
                config.set_telegram_bot_token(new_token)
                console.print(f"  [green]✓[/green] {_('tg.token_saved')}")
            continue

        if choice == 1:
            new_chat = _ainput(f"  [bold]{_('tg.field_chat')}[/bold] [dim]({_('tg.field_chat_hint')}):[/dim] ")
            if new_chat:
                config.set_telegram_chat_id(new_chat)
                console.print(f"  [green]✓[/green] {_('tg.chat_saved')}")
            continue

        if choice == 2:
            if not token:
                console.print(f"  [red]{_('tg.set_token_first')}[/red]")
                continue
            _discover_chat_id(token)
            continue

        if choice == 3:
            if not token or not chat_id:
                console.print(f"  [red]{_('tg.token_and_chat_required')}[/red]")
                continue
            _test_send(token, chat_id)
            continue

        if choice == 4:
            new_enabled = not enabled
            config.set_telegram_enabled(new_enabled)
            console.print(
                f"  [green]✓[/green] {_('tg.bridge_enabled') if new_enabled else _('tg.bridge_disabled')}"
            )
            console.print(
                f"  [dim]{_('tg.restart_to_apply')}[/dim]"
            )
            continue

def _discover_chat_id(token: str) -> None:
    """Запрашивает getUpdates и показывает все chat_id из сообщений."""
    import json
    import urllib.request

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        console.print(f"  [red]✗ Error: {escape(str(e))}[/red]")
        return

    if not data.get("ok"):
        console.print(f"  [red]✗ {escape(str(data))}[/red]")
        return

    updates = data.get("result", [])
    if not updates:
        console.print(f"  [yellow]{_('tg.no_updates')}[/yellow]")
        return

    seen = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post")
        if not msg:
            continue
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        if cid and cid not in seen:
            seen[cid] = (chat.get("type", "?"), title)

    if not seen:
        console.print(f"  [yellow]{_('tg.no_chats_in_updates')}[/yellow]")
        return

    console.print()
    console.print(f"  [bold]{_('tg.discovered')}[/bold]")
    for cid, (ctype, title) in seen.items():
        console.print(f"    [yellow]{cid}[/yellow]  [dim]({escape(ctype)})[/dim]  {escape(title)}")
    console.print()

    new_chat = _ainput(f"  [bold]{_('tg.save_chat')}[/bold] [dim]({_('tg.save_chat_hint')}):[/dim] ")
    if new_chat:
        config.set_telegram_chat_id(new_chat)
        console.print(f"  [green]✓[/green] {_('tg.saved')}")

def _test_send(token: str, chat_id: str) -> None:
    from apis.telegram import get_bridge
    bridge = get_bridge()
    try:
        ok, msg = asyncio.get_event_loop().run_until_complete(
            bridge.test_send(token, int(chat_id), "<b>necli-api</b>: connectivity test ✅")
        )
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            ok, msg = loop.run_until_complete(
                bridge.test_send(token, int(chat_id), "<b>necli-api</b>: connectivity test ✅")
            )
        finally:
            loop.close()
    except ValueError:
        console.print(f"  [red]✗ {_('tg.chat_id_must_be_number')}[/red]")
        return

    if ok:
        console.print(f"  [green]✓[/green] {_('tg.sent')}")
    else:
        console.print(f"  [red]✗ {escape(msg)}[/red]")