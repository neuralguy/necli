"""Telegram inline/reply меню для управления агентом из бота.

Регистрируется при старте interactive-сессии, держит замыкание на `state`
и оркестрирует: переключение режима/модели, новый чат, план, статус.
"""

from __future__ import annotations

import logging

import config
from apis.telegram import get_bridge

logger = logging.getLogger(__name__)

# Маркер для определения, что сообщение из TG было slash-командой,
# которая уже обработана — основной prompt loop его игнорирует.


def _build_main_menu():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧩 Mode", callback_data="menu:mode"),
            InlineKeyboardButton(text="🤖 Model", callback_data="menu:model"),
        ],
        [
            InlineKeyboardButton(text="📋 Plan", callback_data="menu:plan"),
            InlineKeyboardButton(text="📊 Status", callback_data="menu:status"),
        ],
        [
            InlineKeyboardButton(text="↻ New chat", callback_data="menu:new"),
            InlineKeyboardButton(text="🗜 Compress", callback_data="menu:compress"),
        ],
        [
            InlineKeyboardButton(text="■ Stop", callback_data="menu:stop"),
            InlineKeyboardButton(text="✕ Close", callback_data="menu:close"),
        ],
    ])


# Текстовые лейблы кнопок reply-клавиатуры → slash-команды.
# Когда пользователь жмёт кнопку, в чат уходит её текст, и мы его маппим в команду.
_REPLY_BUTTON_TO_CMD = {
    "🎛 Menu": "/menu",
    "■ Stop": "/stop",
}


def _build_reply_keyboard():
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎛 Menu"), KeyboardButton(text="■ Stop")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="type a task or press 🎛 Menu",
    )


def _build_mode_menu(current_mode: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    modes = [("agent", "🚀 agent"), ("planning", "🧠 planning")]
    rows = []
    for mid, label in modes:
        mark = "● " if mid == current_mode else "  "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{label}", callback_data=f"mode:{mid}",
        )])
    rows.append([InlineKeyboardButton(text="← Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_model_menu(current_model_id: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from apis.registry import get_definition
    api_id = config.get_active_api()
    defn = get_definition(api_id) if api_id else None
    rows = []
    if defn:
        for m in defn.models[:20]:
            mark = "● " if m.id == current_model_id else "  "
            label = f"{mark}{m.display_name}"
            if len(label) > 50:
                label = label[:49] + "…"
            rows.append([InlineKeyboardButton(
                text=label, callback_data=f"model:{m.id}",
            )])
    if not rows:
        rows.append([InlineKeyboardButton(
            text="No models", callback_data="menu:main",
        )])
    rows.append([InlineKeyboardButton(text="← Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_status(state) -> str:
    import html
    s = state.session
    api_id = config.get_active_api() or "—"
    mode = state.mode_state.get("mode", "agent")
    busy = getattr(get_bridge(), "agent_busy", False)
    state_line = "⚙️ <b>working…</b>" if busy else "🟢 <b>idle</b>"
    parts = [
        "<b>📊 Status</b>",
        f"  <b>Agent:</b> {state_line}",
        f"  <b>Mode:</b> <code>{html.escape(mode)}</code>",
        f"  <b>Model:</b> <code>{html.escape(state.cur_model)}</code>",
        f"  <b>API:</b> <code>{html.escape(api_id)}</code>",
        f"  <b>Workdir:</b> <code>{html.escape(state.workdir)}</code>",
        f"  <b>Messages:</b> {s.message_count}",
        f"  <b>Tokens:</b> ↑{s.raw_input_tokens} ↓{s.output_tokens}",
        f"  <b>Cost:</b> ≈${s.total_cost:.4f}",
    ]
    return "\n".join(parts)


def _format_plan(state) -> str:
    import html
    from agent import get_current_ctx
    ctx = get_current_ctx()
    plan = ctx.plan if ctx else None
    if not plan or not plan.steps:
        return "<i>No active plan</i>"
    from planner import StepStatus
    icons = {
        StepStatus.PENDING: "⏳",
        StepStatus.IN_PROGRESS: "▶️",
        StepStatus.DONE: "✅",
        StepStatus.SKIPPED: "⏭",
    }
    lines = [f"<b>📋 Plan</b> [{plan.progress_str}]"]
    if plan.goal:
        lines.append(f"<i>{html.escape(plan.goal[:200])}</i>")
    for i, st in enumerate(plan.steps):
        icon = icons.get(st.status, "•")
        title = st.title[:200]
        line = f"{icon} <b>{i}.</b> {html.escape(title)}"
        if st.notes:
            line += f" — <i>{html.escape(st.notes[:100])}</i>"
        lines.append(line)
    return "\n".join(lines)


async def _do_new_chat(state) -> None:
    """Запрашивает новый чат: прерывает агента (если работает) и
    шлёт спец-маркер в очередь TG-сообщений, чтобы main loop обработал
    new_chat синхронно в своём контексте (без гонки с prompt_toolkit).
    """
    from agent import get_current_ctx
    from apis.telegram import IncomingMessage
    bridge = get_bridge()
    ctx = get_current_ctx()
    if ctx is not None:
        ctx.interrupted = True
        ctx.hard_interrupted = True
    state._tg_new_chat_requested = True
    if bridge.is_running and bridge.incoming_queue is not None:
        # Спец-маркер: main loop увидит его в _read_user_with_tg и обработает.
        try:
            bridge.incoming_queue.put_nowait(IncomingMessage(
                text="__tg_action__:new_chat",
                chat_id=bridge.chat_id or 0,
                user_id=0,
                username="",
            ))
        except Exception as e:
            logger.warning("tg new_chat queue put failed: %s", e)
            bridge.send("⚠️ <i>new chat request dropped (queue busy)</i>")


def register_tg_menu(state) -> None:
    """Регистрирует команды и callback-обработчики в bridge."""
    bridge = get_bridge()
    if not bridge.is_running:
        return

    def _menu_title() -> str:
        busy = getattr(bridge, "agent_busy", False)
        sub = "⚙️ <i>agent working…</i>" if busy else "🟢 <i>idle</i>"
        return f"🎛 <b>Control menu</b>\n{sub}"

    async def cmd_menu(arg, message):
        bridge.send(_menu_title(), reply_markup=_build_main_menu())

    async def cmd_status(arg, message):
        bridge.send(_format_status(state))

    async def cmd_plan(arg, message):
        bridge.send(_format_plan(state))

    async def cmd_stop(arg, message):
        from agent import get_current_ctx
        ctx = get_current_ctx()
        if ctx:
            ctx.interrupted = True
            ctx.hard_interrupted = True
            bridge.send("■ <b>Agent stop requested</b>")
        else:
            bridge.send("<i>No active agent</i>")

    async def cmd_new(arg, message):
        await _do_new_chat(state)

    async def cmd_start(arg, message):
        bridge.send(
            "🟢 <b>necli-api connected</b>\n"
            "Type a task — it will be sent to the agent in the terminal.\n"
            "Use the buttons or 🎛 Menu to control.",
            reply_markup=_build_reply_keyboard(),
        )

    bridge.register_command("/menu", cmd_menu)
    bridge.register_command("/status", cmd_status)
    bridge.register_command("/plan", cmd_plan)
    bridge.register_command("/stop", cmd_stop)
    bridge.register_command("/new", cmd_new)
    bridge.register_command("/start", cmd_start)
    bridge.register_button_aliases(_REPLY_BUTTON_TO_CMD)

    async def on_callback(data: str, cb):
        await bridge.answer_callback(cb)

        if data == "menu:main":
            await bridge.edit_inline_message(
                cb, _menu_title(),
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:close":
            try:
                await cb.message.delete()
            except Exception:
                pass
            return

        if data == "menu:status":
            await bridge.edit_inline_message(
                cb, _format_status(state),
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:plan":
            await bridge.edit_inline_message(
                cb, _format_plan(state),
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:mode":
            current = state.mode_state.get("mode", "agent")
            await bridge.edit_inline_message(
                cb, "<b>🧩 Select mode</b>",
                reply_markup=_build_mode_menu(current),
            )
            return

        if data.startswith("mode:"):
            new_mode = data.split(":", 1)[1]
            if new_mode not in ("agent", "planning"):
                await bridge.answer_callback(cb, "Unknown mode", show_alert=True)
                return
            state.mode_state["mode"] = new_mode
            state.mode_state["changed"] = True
            from agent import get_current_ctx
            ctx = get_current_ctx()
            if ctx:
                ctx.mode = new_mode
            # синхронизируем с prompt'ом терминала
            if getattr(state, "prompt_input", None) is not None:
                state.prompt_input.mode = new_mode
            await bridge.edit_inline_message(
                cb, f"✅ Mode switched: <b>{new_mode}</b>",
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:model":
            await bridge.edit_inline_message(
                cb, "<b>🤖 Select model</b>",
                reply_markup=_build_model_menu(config.get_active_api_model()),
            )
            return

        if data.startswith("model:"):
            model_id = data.split(":", 1)[1]
            from apis.registry import get_definition
            from apis.agent_adapter import create_api_session
            api_id = config.get_active_api()
            defn = get_definition(api_id) if api_id else None
            if not defn:
                await bridge.answer_callback(cb, "API not configured", show_alert=True)
                return
            mi = defn.get_model_info(model_id)
            if not mi:
                await bridge.answer_callback(cb, "Model not found", show_alert=True)
                return
            config.set_active_api_model(model_id)
            try:
                create_api_session(api_id, model_id)
            except Exception as e:
                await bridge.answer_callback(cb, f"Error: {e}", show_alert=True)
                return
            state.cur_model = mi.display_name
            state.msg_num = 0
            await bridge.edit_inline_message(
                cb, f"✅ Model: <b>{mi.display_name}</b>\n<code>{model_id}</code>",
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:new":
            await _do_new_chat(state)
            await bridge.edit_inline_message(
                cb, "↻ <b>New chat created</b>",
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:compress":
            state._tg_compress_requested = True
            await bridge.edit_inline_message(
                cb,
                "🗜 History compression requested — will apply after the agent's next response.",
                reply_markup=_build_main_menu(),
            )
            return

        if data == "menu:stop":
            from agent import get_current_ctx
            ctx = get_current_ctx()
            if ctx:
                ctx.interrupted = True
                ctx.hard_interrupted = True
                await bridge.answer_callback(cb, "Stop requested", show_alert=False)
            else:
                await bridge.answer_callback(cb, "No active agent", show_alert=False)
            return

        await bridge.answer_callback(cb, f"Unknown command: {data}")

    bridge.register_callback_handler(on_callback)

    bridge.set_bot_commands_sync([
        ("menu", "Main menu"),
        ("status", "Current status"),
        ("plan", "Active plan"),
        ("new", "New chat"),
        ("stop", "Stop agent"),
    ])