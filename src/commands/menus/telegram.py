"""Menu /telegram — Telegram bridge configuration.

Состояние моста (токен, чат, флаги) — шапка виджета, а не запись в scrollback:
раньше она печаталась туда при каждом возврате в меню.
"""

import asyncio

import config
from commands.menus._style import card_menu, facts_line
from config.i18n import t as _
from ui import overlays


def _mask_token(token: str) -> str:
    if not token:
        return "—"
    if len(token) <= 12:
        return token[:4] + "…"
    return token[:6] + "…" + token[-4:]


def _flag(v: bool) -> str:
    return _("tg.on") if v else _("tg.off")


async def telegram_interactive():
    """Интерактивное меню Telegram-моста.

    Возвращает новое желаемое состояние enabled (bool), если пользователь
    переключил включение/выключение — вызывающий код применит его на лету
    (запустит/остановит бридж). Возвращает None, если ничего не меняли.
    """
    toggled = None
    while True:
        token = config.get_telegram_bot_token()
        chat_id = config.get_telegram_chat_id()
        enabled = config.get_telegram_enabled()

        from apis.telegram import get_bridge
        running = get_bridge().is_running

        show_thinking = config.get_telegram_show_thinking()
        tool_io = config.get_telegram_tool_io()
        assistant_header = config.get_telegram_assistant_header()
        approve = config.get_telegram_approve()

        items = [
            {"label": _("tg.set_token"), "hint": _("tg.set_token_hint"),
             "badge": _mask_token(token), "badge_style": "dim"},
            {"label": _("tg.set_chat"), "hint": _("tg.set_chat_hint"),
             "badge": chat_id or "—", "badge_style": "dim"},
            {"label": _("tg.discover"), "hint": _("tg.discover_hint")},
            {"label": _("tg.test_send"), "hint": _("tg.test_send_hint")},
            {"label": _("tg.disable") if enabled else _("tg.enable"),
             "hint": _("tg.enable_hint"),
             "icon": "●" if enabled else "○",
             "icon_style": "success" if enabled else "muted"},
            {"label": _("tg.show_thinking"), "hint": _("tg.show_thinking_hint"),
             "badge": _flag(show_thinking),
             "badge_style": "success" if show_thinking else "dim"},
            {"label": _("tg.tool_io"), "hint": _("tg.tool_io_hint"),
             "badge": _flag(tool_io), "badge_style": "success" if tool_io else "dim"},
            {"label": _("tg.assistant_header"), "hint": _("tg.assistant_header_hint"),
             "badge": _flag(assistant_header),
             "badge_style": "success" if assistant_header else "dim"},
            {"label": _("tg.approve"), "hint": _("tg.approve_hint"),
             "badge": _flag(approve), "badge_style": "success" if approve else "dim"},
            {"label": _("common.back")},
        ]
        choice = await card_menu(
            items,
            title=_("tg.title"),
            status=_("tg.on") if enabled else _("tg.off"),
            status_style="success" if enabled else "muted",
            facts=[facts_line(
                _("tg.header"),
                _("tg.bot_running") if running else _("tg.bot_stopped"),
                f"{_('tg.token_label')} {_mask_token(token)}",
                f"{_('tg.chat_id_label')} {chat_id or '—'}",
            )],
        )
        if choice is None or choice == 9:
            return toggled

        if choice == 0:
            # Токен бота — такой же секрет, как ключ API: вводим под маской,
            # чтобы он не остался в scrollback открытым текстом.
            new_token = await overlays.ask_text(f"{_('tg.field_token')}:", password=True)
            if new_token:
                config.set_telegram_bot_token(new_token)
            continue

        if choice == 1:
            new_chat = await overlays.ask_text(
                f"{_('tg.field_chat')} ({_('tg.field_chat_hint')}):")
            if new_chat:
                config.set_telegram_chat_id(new_chat)
            continue

        if choice == 2:
            if not token:
                continue
            await _discover_chat_id(token)
            continue

        if choice == 3:
            if not token or not chat_id:
                continue
            await _test_send(token, chat_id)
            continue

        if choice == 4:
            new_enabled = not enabled
            config.set_telegram_enabled(new_enabled)
            toggled = new_enabled
            continue

        if choice == 5:
            config.set_telegram_show_thinking(not show_thinking)
            continue
        if choice == 6:
            config.set_telegram_tool_io(not tool_io)
            continue
        if choice == 7:
            config.set_telegram_assistant_header(not assistant_header)
            continue
        if choice == 8:
            config.set_telegram_approve(not approve)
            continue


async def _discover_chat_id(token: str) -> None:
    """Запрашивает getUpdates и показывает все chat_id из сообщений."""
    import json
    import urllib.request

    url = f"https://api.telegram.org/bot{token}/getUpdates"

    def _fetch() -> dict:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        # Сетевой вызов синхронный, поэтому уводим его в поток: иначе он
        # заморозил бы весь Application вместе со стримом агента.
        data = await asyncio.to_thread(_fetch)
    except Exception:
        return

    if not data.get("ok"):
        return

    updates = data.get("result", [])
    if not updates:
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
        return

    # Найденные чаты — выбираем из списка, а не переписываем id руками.
    items = [{"label": str(cid), "hint": title, "badge": ctype, "badge_style": "dim"}
             for cid, (ctype, title) in seen.items()]
    items.append({"label": _("common.cancel")})
    pick = await card_menu(items, title=_("tg.discovered"),
                           facts=[_("tg.save_chat_hint")])
    if pick is not None and pick < len(seen):
        chosen = list(seen.keys())[pick]
        config.set_telegram_chat_id(str(chosen))


async def _test_send(token: str, chat_id: str) -> None:
    from apis.telegram import get_bridge
    bridge = get_bridge()
    # chat_id парсим один раз ДО отправки, чтобы ValueError от некорректного
    # ввода перехватывался единообразно (раньше парсинг во fallback-ветке мог
    # выбросить ValueError мимо обработчика и уронить меню).
    try:
        cid = int(chat_id)
    except ValueError:
        return

    # Раньше здесь стоял asyncio.run(): из интерактивного цикла, у которого
    # уже есть работающий loop, он падал с RuntimeError. Теперь просто await.
    await bridge.test_send(token, cid, "<b>necli-api</b>: connectivity test ✅")
