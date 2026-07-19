"""Интерактивный диалог со стримингом — API-only.

Обработка SlashResult — в commands/slash_handler.py.
InteractiveState — в commands/interactive_state.py.
Сборка status-line — в commands/interactive_status.py.
"""

import asyncio
import logging
import os

import click
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

import agent as gsagent
import config
import models as app_models
from agent import get_current_ctx
from apis.telegram import get_bridge as _get_tg_bridge
from commands.helpers import (
    _print_response_separator,
    _print_user_message,
    _print_welcome,
    _resolve_or_exit,
    _run_with_interrupt,
)
from commands.interactive_state import InteractiveState
from commands.interactive_status import build_status_line
from commands.slash import _handle_slash
from commands.slash_handler import handle_slash_result
from config.i18n import t as tr
from session import Session
from tools.ssh import close_all_connections
from ui.clipboard import cleanup_old_images
from ui.file_context import expand_at_references
from ui.prompt import _BG_RESUME, _EOF, InputPrompt

logger = logging.getLogger(__name__)
console = Console()


def _start_data_cleanup() -> None:
    """Тихая фоновая очистка мусора из .data (не чаще раза в сутки).

    В фоне — обход больших undo-репозиториев не должен задерживать старт. Любые
    ошибки внутри maybe_cleanup проглатываются, поэтому поток безопасен.
    """
    import threading

    def _worker() -> None:
        try:
            from config.data_cleanup import maybe_cleanup
            maybe_cleanup()
        except Exception:
            logger.debug("data cleanup worker failed", exc_info=True)

    try:
        threading.Thread(target=_worker, name="necli-data-cleanup", daemon=True).start()
    except Exception:
        logger.debug("failed to start data cleanup thread", exc_info=True)


def _set_activity_status(state: InteractiveState, status: str) -> None:
    state.activity_status = status
    prompt_input = getattr(state, "prompt_input", None)
    if prompt_input is not None and hasattr(prompt_input, "set_activity_status"):
        prompt_input.set_activity_status(status, state.session)
        return
    try:
        from ui.terminal_title import set_session_terminal_title
        set_session_terminal_title(state.session, status)
    except Exception:
        logger.debug("terminal activity status update failed", exc_info=True)


@click.command(name="cli")
@click.option("--model", "-m", default=None)
@click.option("--workdir", "-w", default=None)
@click.option("--resume", "-r", default=None)
@click.option("--api", "-A", "api_provider", default=None,
              help="API provider (e.g. openai, anthropic). Activates the selected provider on startup.")
def interactive(model, workdir, resume, api_provider):
    """Interactive chat session (API-only)."""

    if api_provider:
        from apis.registry import get_definition, reload_providers
        reload_providers()
        defn = get_definition(api_provider)
        if not defn:
            console.print(f"[red]{tr('boot.api_not_found', name=api_provider)}[/red]")
            console.print(f"[dim]{tr('boot.add_via_api')}[/dim]")
            return
        saved_model = config.get_active_api_model() if config.get_active_api() == api_provider else ""
        if saved_model and defn.get_model_info(saved_model):
            api_model = saved_model
        else:
            api_model = defn.default_model or (defn.models[0].id if defn.models else "")
        if not api_model:
            console.print(f"[red]{tr('boot.no_models_for', name=api_provider)}[/red]")
            return
        config.set_active_api(api_provider)
        config.set_active_api_model(api_model)

    from commands.onboarding import _ensure_default_provider, needs_onboarding, run_onboarding
    if needs_onboarding():
        run_onboarding()
    elif not config.get_active_api():
        _ensure_default_provider()

    if model:
        model = _resolve_or_exit(model)
    else:
        model = config.get("model", config.TARGET_MODEL)
        resolved = app_models.resolve_model(model)
        model = resolved if resolved else config.TARGET_MODEL

    workdir = workdir or os.getcwd()
    cleanup_old_images()
    _start_data_cleanup()

    async def _run():
        loop = asyncio.get_running_loop()
        _orig_exception_handler = loop.get_exception_handler()

        def _quiet_exception_handler(loop, context):
            exc = context.get("exception")
            if exc and isinstance(exc, (BrokenPipeError, ConnectionError, OSError)):
                return
            if _orig_exception_handler:
                _orig_exception_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_quiet_exception_handler)

        if resume:
            from session import storage as _storage
            session = _storage.load(resume)
            if not session:
                console.print(f"[red]{tr('boot.session_not_found', name=resume)}[/red]")
                return
        else:
            session = Session()

        _think_on_startup = bool(config.get("think_enabled", False))
        state = InteractiveState(
            session=session,
            msg_num=session.message_count,
            cur_model=model,
            workdir=workdir,
            think_enabled=_think_on_startup,
        )
        # THINK на старте НЕ требует one-shot сигнала: системный промт
        # пересобирается из config и уже содержит THINK-блок, если флаг
        # включён. Сигнал в поток нужен только при переключении НА ЛЕТУ
        # (state.think_changed выставляется в /think-хендлере).

        try:
            from apis.agent_adapter import create_api_session, restore_api_session_history
            from apis.registry import get_definition
            _api_id = config.get_active_api()
            _api_model = config.get_active_api_model()
            create_api_session(_api_id, _api_model)
            _defn = get_definition(_api_id)
            if _defn and _api_model:
                _minfo = _defn.get_model_info(_api_model)
                state.cur_model = _minfo.display_name if _minfo else _api_model
            elif _api_model:
                state.cur_model = _api_model
            _resume_loaded = 0
            if resume and session.message_count > 0:
                _resume_loaded = restore_api_session_history(session)
                state.msg_num = session.message_count

            # ── LSP servers (инициализация до welcome — счётчик идёт в панель) ──
            n_lsp = 0
            try:
                from apis.lsp_client import init_lsp_from_config
                n_lsp = init_lsp_from_config()
            except Exception as e:
                logger.error("lsp init failed: %s", e, exc_info=True)

            # ── MCP servers (инициализация до welcome — счётчик идёт в панель) ──
            n_mcp = 0
            mcp_tools = 0
            mcp_errors: list[tuple[str, str]] = []
            try:
                from apis.mcp_client import init_mcp_from_config, list_mcp_servers
                n_mcp = init_mcp_from_config()
                if n_mcp > 0:
                    infos = list_mcp_servers()
                    mcp_tools = sum(i.get("tool_count", 0) for i in infos if i.get("status") == "connected")
                    mcp_errors = [(i["id"], i.get("error", "")) for i in infos if i.get("status") == "error"]
            except Exception as e:
                logger.error("mcp init failed: %s", e, exc_info=True)

            # ── Telegram bridge (если включён) — стартуем ДО welcome, чтобы
            # статус бота попал в шапку рядом с lsp/mcp ──
            tg_bridge = _get_tg_bridge()
            tg_info = ""
            tg_warn = ""
            if config.get_telegram_enabled():
                tg_token = config.get_telegram_bot_token()
                tg_chat = config.get_telegram_chat_id()
                if tg_token and tg_chat:
                    try:
                        ok, info = await tg_bridge.start(tg_token, int(tg_chat))
                        if ok:
                            tg_info = info
                            from agent.tg_menu import _build_reply_keyboard, register_tg_menu
                            register_tg_menu(state)
                            tg_bridge.send(
                                f"🟢 <b>necli-api</b> started\n"
                                f"<i>{escape(workdir)}</i>\n"
                                f"model: <code>{escape(state.cur_model)}</code>\n\n"
                                f"Controls: /menu",
                                reply_markup=_build_reply_keyboard(),
                            )
                        else:
                            tg_warn = f"  [yellow]⚠ Telegram: {escape(info)}[/yellow]"
                    except Exception as e:
                        tg_warn = f"  [yellow]⚠ Telegram: {escape(str(e))}[/yellow]"
                        logger.error("tg start failed: %s", e, exc_info=True)
                else:
                    tg_warn = f"  [dim]{tr('boot.telegram_enabled_not_configured')}[/dim]"

            # Captureим welcome в строку, сохраняем для replay, печатаем в stdout
            with console.capture() as _wcap:
                _print_welcome(state.cur_model, session, workdir=workdir, n_lsp=n_lsp,
                               n_mcp=n_mcp, mcp_tools=mcp_tools, tg_info=tg_info)
            _welcome_text = _wcap.get()
            if _welcome_text:
                console.print(_welcome_text, end="", highlight=False, markup=False)
            try:
                import agent.render_replay as _rr
                _rr._LAST_WELCOME_CAPTURE = _welcome_text
            except Exception:
                logger.debug("store welcome capture failed", exc_info=True)

            for _sid, _err in mcp_errors:
                console.print(f"  [yellow]⚠ MCP/{_sid}:[/yellow] [dim]{escape(_err)}[/dim]")

            if tg_warn:
                console.print(tg_warn)

            if resume and _resume_loaded:
                try:
                    from agent.render_replay import print_session_history
                    print_session_history(session, max_messages=20)
                except Exception:
                    logger.debug("print_session_history failed", exc_info=True)

            def _toggle_mode(new_mode):
                state.mode_state["mode"] = new_mode
                state.mode_state["changed"] = True
                ctx = get_current_ctx()
                if ctx:
                    ctx.mode = new_mode

            state.prompt_input = InputPrompt(working_dir=workdir, on_mode_toggle=_toggle_mode)
            state.prompt_input.session = state.session
            _set_activity_status(state, "idle")
            # Привязываем asyncio-loop к фоновым задачам: завершившаяся в фоне
            # задача сможет разбудить ожидание ввода (авто-резюм агента).
            try:
                from tools.background import register_event_loop
                register_event_loop(asyncio.get_running_loop())
            except Exception:
                logger.debug("background event-loop register failed", exc_info=True)
            # Привязываем prompt к текущему ctx (для reprint separator после Ctrl+O replay).
            try:
                _ctx0 = get_current_ctx()
                if _ctx0 is not None:
                    _ctx0.prompt_input = state.prompt_input
                    # Callback пересчёта статуса на Ctrl+O reprint (после
                    # compress/decompress last_status_text устаревает).
                    _ctx0.rebuild_status = lambda: build_status_line(state)
            except Exception:
                logger.debug("bind ctx prompt/rebuild_status failed", exc_info=True)

            while True:
                if state.activity_status not in ("done", "poll"):
                    _set_activity_status(state, "idle")
                status = build_status_line(state)
                # Кладём в ctx для Ctrl+O reprint после replay.
                try:
                    state.prompt_input.status_provider = lambda: build_status_line(state)
                    _ctx_s = get_current_ctx()
                    if _ctx_s is not None:
                        _ctx_s.last_status_text = status
                        # ctx пересоздаётся в run_agent — переустанавливаем
                        # привязки каждый цикл, иначе после compress/agent-run
                        # Ctrl+O видит ctx без prompt_input/rebuild_status.
                        _ctx_s.prompt_input = state.prompt_input
                        _ctx_s.rebuild_status = lambda: build_status_line(state)
                except Exception:
                    logger.debug("rebind ctx status each loop failed", exc_info=True)

                user = await _read_user_with_tg(state, status, tg_bridge)

                if user is _EOF:
                    console.print(f"\n  [dim]{tr('common.bye')}[/dim]")
                    break

                if user is _BG_RESUME:
                    # Фоновая задача завершилась, пока ждали ввода — будим агента
                    # с её результатом, без участия пользователя.
                    if await _resume_agent_for_background(state, tg_bridge):
                        _print_response_separator()
                        await _print_recap_if_ready(state)
                    continue

                if user is None or not user:
                    continue

                _set_activity_status(state, "idle")

                if user.startswith("/"):
                    # Captureим вывод slash-команды, сохраняем в render_store
                    # как raw_console item — чтобы Ctrl+O replay показал команды.
                    with console.capture() as _cap:
                        act = _handle_slash(user, state.cur_model, state.session, state.last_elapsed)
                        await handle_slash_result(act, state)
                    _captured = _cap.get()
                    # Печатаем как было
                    if _captured:
                        console.print(_captured, end="", highlight=False, markup=False)
                    try:
                        _ctx = get_current_ctx()
                        if _ctx is not None and getattr(_ctx, "render_store", None) is not None:
                            _ctx.render_store.add("raw_console", {
                                "command": user,
                                "output": _captured or "",
                            })
                    except Exception:
                        logger.debug("store slash raw_console failed", exc_info=True)
                    continue

                # ── Send message ──

                _set_activity_status(state, "working")
                _print_user_message(user, state.cur_model)

                # Зеркалим в TG только ввод из терминала (из TG он уже виден в чате).
                if not getattr(state, "_last_input_from_tg", False):
                    try:
                        if tg_bridge.is_running:
                            from agent.telegram_handler import TelegramEventHandler
                            TelegramEventHandler(None).mirror_user(user)
                    except Exception:
                        logger.debug("tg mirror_user failed", exc_info=True)

                state.msg_num += 1

                _maybe_launch_recap(state)
                _maybe_extract_memory(state)

                message_images = state.prompt_input.get_and_clear_images()

                # add_user_message может переименовать (переместить) папку сессии
                user_message = state.session.add_user_message(user, model=state.cur_model)

                # при первом сообщении — картинки лежат внутри session.dir, их
                # абсолютные пути устаревают. Перенаправляем на актуальную папку.
                if message_images:
                    from pathlib import Path as _Path
                    sess_imgs = _Path(state.session.dir) / "clipboard_images"
                    fixed = []
                    for p in message_images:
                        p = _Path(p)
                        candidate = sess_imgs / p.name
                        fixed.append(candidate if candidate.exists() else p)
                    message_images = fixed
                    user_message.attachments = [
                        {
                            "path": str(p),
                            "name": p.name,
                            "mime": "image/png",
                            "is_image": True,
                        }
                        for p in message_images
                    ]
                try:
                    from agent.context import AgentContext
                    from agent.loop import set_current_ctx
                    _ctx = get_current_ctx()
                    if _ctx is None:
                        _ctx = AgentContext(working_dir=state.workdir, mode=state.mode_state.get("mode", "agent"))
                        set_current_ctx(_ctx)
                    _ctx.render_store.add_user(user, status=status)
                except Exception:
                    import logging as _lg
                    _lg.getLogger("agent.render_store").exception("add_user failed")

                agent_message = user
                # Маппинг [imageN] → реальный путь, чтобы агент мог открыть
                # вставленные картинки как файлы через инструменты (read_files и др.).
                if message_images:
                    image_lines = [
                        f"[image{i}] = {p}"
                        for i, p in enumerate(message_images, start=1)
                    ]
                    image_block = (
                        "--- inserted images (open with file tools by path) ---\n"
                        + "\n".join(image_lines)
                        + "\n--- end inserted images ---"
                    )
                    agent_message = image_block + "\n\n" + agent_message
                _, file_context_block, file_refs = expand_at_references(user, state.workdir)
                if file_context_block:
                    ref_names = [r.raw for r in file_refs if not r.error]
                    files_str = ', '.join(ref_names[:5]) + ('...' if len(ref_names) > 5 else '')
                    console.print(
                        f"  [dim]📄 {tr('send.context_files', files=files_str)}[/dim]"
                    )
                    agent_message = file_context_block + "\n\n" + agent_message

                # Полное описание mode/think — в системном промте (пересобирается
                # каждый запрос). В поток шлём ТОЛЬКО короткий one-shot сигнал при
                # переключении, чтобы модель явно заметила смену в середине диалога.
                if state.mode_state["changed"]:
                    from prompts import (
                        MODE_SWITCH_TO_AGENT,
                        MODE_SWITCH_TO_AUTONOMOUS,
                        MODE_SWITCH_TO_PLANNING,
                    )
                    if state.mode_state["mode"] == "planning":
                        mode_notice = MODE_SWITCH_TO_PLANNING
                    elif state.mode_state["mode"] == "autonomous":
                        mode_notice = MODE_SWITCH_TO_AUTONOMOUS
                    else:
                        mode_notice = MODE_SWITCH_TO_AGENT
                    agent_message = mode_notice + "\n\n" + agent_message
                    state.mode_state["changed"] = False

                if state.think_changed:
                    from prompts import THINK_SWITCH_OFF, THINK_SWITCH_ON
                    notice = THINK_SWITCH_ON if state.think_enabled else THINK_SWITCH_OFF
                    agent_message = notice + "\n\n" + agent_message
                    state.think_changed = False

                history_for_msg = None
                if state.pending_context:
                    history_for_msg = state.pending_context
                    state.pending_context = None

                is_cont = state.msg_num > 1

                try:
                    from agent.undo_store import snapshot_round
                    snapshot_round(state.workdir, label=user[:80])
                except Exception:
                    logger.debug("undo snapshot failed", exc_info=True)

                coro = gsagent.run_agent_interactive(
                    agent_message, model=state.cur_model, working_dir=state.workdir,
                    is_continuation=is_cont,
                    session=state.session, history=history_for_msg,
                    images=message_images if message_images else None,
                    mode=state.mode_state["mode"],
                )

                _cancelled = False
                try:
                    state.last_elapsed, _cancelled = await _run_with_interrupt(coro, state.session)
                    _set_activity_status(state, "idle" if _cancelled else "done")
                except Exception as e:
                    _set_activity_status(state, "idle")
                    console.print(f"\n  [red]{tr('send.error_run', error=str(e))}[/red]")

                _print_response_separator()

                await _print_recap_if_ready(state)

                # ── Авто-компрессия при ≥90% контекстного лимита ──
                await _maybe_auto_compress(state)

                # ── Отложенные запросы из Telegram-меню ──
                if getattr(state, "_tg_compress_requested", False):
                    state._tg_compress_requested = False
                    await _handle_tg_compress(state)

        finally:
            from session import storage as _storage
            _storage.save(state.session)
            try:
                closed = close_all_connections()
                if closed:
                    console.print(f"  [dim]{tr('send.ssh_closed', n=closed)}[/dim]")
            except Exception:
                logger.debug("close_all_connections failed", exc_info=True)
            try:
                from apis.mcp_client import shutdown_mcp
                shutdown_mcp()
            except Exception:
                logger.debug("mcp shutdown failed", exc_info=True)
            try:
                from apis.lsp_client import shutdown_lsp
                shutdown_lsp()
            except Exception:
                logger.debug("lsp shutdown failed", exc_info=True)
            try:
                from agent.undo_store import cleanup_store
                cleanup_store(state.workdir)
            except Exception:
                logger.debug("undo cleanup failed", exc_info=True)
            try:
                tg = _get_tg_bridge()
                if tg.is_running:
                    tg.send("🔴 <b>necli-api</b> stopped")
                    await tg.stop()
            except Exception:
                logger.debug("tg stop failed", exc_info=True)

    asyncio.run(_run())


async def _apply_tg_action(state: InteractiveState, action: str) -> None:
    """Выполняет отложенное TG-действие в контексте main loop (безопасно для prompt_toolkit)."""
    from apis.telegram import get_bridge
    bridge = get_bridge()
    try:
        if action == "new_chat":
            from commands.slash_handler import _handle_new_chat
            await _handle_new_chat(state)
            if bridge.is_running:
                bridge.send("↻ <b>New chat created</b>")
        elif action == "compress":
            from commands.slash_handler import _handle_compress
            await _handle_compress(state)
            if bridge.is_running:
                bridge.send("🗜 <b>History compressed</b>")
        else:
            logger.warning("unknown tg action: %s", action)
    except Exception as e:
        logger.error("tg action %s failed: %s", action, e, exc_info=True)
        if bridge.is_running:
            bridge.send(f"❌ <i>tg action {action}: {e}</i>")


async def _handle_tg_compress(state: InteractiveState):
    from commands.slash_handler import _handle_compress
    try:
        await _handle_compress(state)
        from apis.telegram import get_bridge
        b = get_bridge()
        if b.is_running:
            b.send("🗜 <b>History compressed</b>")
    except Exception as e:
        logger.error("tg compress failed: %s", e, exc_info=True)


_AUTO_COMPRESS_THRESHOLD = 0.90
_RECAP_EVERY = 5
_MEMORY_EXTRACT_EVERY = 6


def _maybe_extract_memory(state: InteractiveState) -> None:
    """Каждые N сообщений запускает фоновое извлечение долговременной памяти.

    Fire-and-forget: результат (число сохранённых фактов) только логируется,
    UI не блокируется и не засоряется. Ошибки внутри проглатываются.
    """
    if state.msg_num <= 0 or state.msg_num % _MEMORY_EXTRACT_EVERY != 0:
        return
    try:
        transcript = state.session.build_compress_text()
    except Exception:
        logger.debug("memory extract transcript build failed", exc_info=True)
        return
    if not transcript.strip():
        return

    workdir = getattr(state.session, "working_dir", None) or os.getcwd()

    async def _run_extract():
        try:
            from memory import extract_memories
            n = await extract_memories(transcript, working_dir=workdir)
            if n:
                logger.info("memory extract: saved %d fact(s) at msg #%d", n, state.msg_num)
        except Exception as e:
            logger.debug("memory extract failed: %s", e, exc_info=True)

    try:
        asyncio.ensure_future(_run_extract())  # noqa: RUF006
    except Exception:
        logger.debug("memory extract launch failed", exc_info=True)


def _maybe_launch_recap(state: InteractiveState) -> None:
    """На каждом N-м пользовательском сообщении запускает фоновый рекап диалога.

    Транскрипт берём ДО ответа текущего раунда (история на момент запроса).
    Результат печатается после ответа основной модели в _print_recap_if_ready.
    """
    if state.msg_num <= 0 or state.msg_num % _RECAP_EVERY != 0:
        return
    try:
        transcript = state.session.build_compress_text()
    except Exception:
        logger.debug("recap transcript build failed", exc_info=True)
        return
    if not transcript.strip():
        return

    from apis.agent_adapter import api_recap

    async def _run_recap():
        try:
            return await api_recap(transcript)
        except Exception as e:
            logger.debug("recap generation failed: %s", e, exc_info=True)
            return ""

    try:
        state.recap_task = asyncio.ensure_future(_run_recap())
        logger.info("recap launched at msg #%d (session=%s)", state.msg_num, state.session.id[:16])
    except Exception:
        logger.debug("recap task launch failed", exc_info=True)
        state.recap_task = None


async def _print_recap_if_ready(state: InteractiveState) -> None:
    """Дожидается фоновую задачу рекапа и печатает её светло-серым курсивом."""
    task = state.recap_task
    if task is None:
        return
    state.recap_task = None
    try:
        text = await task
    except Exception:
        logger.debug("recap await failed", exc_info=True)
        return
    if not text or not text.strip():
        return
    console.print()
    console.print(f"[italic grey62]📋 {escape(text.strip())}[/italic grey62]")


async def _maybe_auto_compress(state: InteractiveState) -> None:
    """Если контекст занят на ≥90% от лимита модели — автоматически сжимает историю."""
    from commands.slash_handler import _handle_compress
    from models import get_context_limit

    try:
        ctx_tokens = state.session.context_tokens
        ctx_limit = get_context_limit(state.cur_model) or 200_000
        if ctx_limit <= 0:
            return
        ratio = ctx_tokens / ctx_limit
        if ratio < _AUTO_COMPRESS_THRESHOLD:
            return
        # Защита от повторного срабатывания на той же сессии без новых сообщений
        last_at = getattr(state, "_auto_compress_last_msg", -1)
        if last_at == state.session.message_count:
            return
        logger.info(
            "auto-compress trigger: session={} ctx={}/{} ({:.0%})",
            state.session.id[:16], ctx_tokens, ctx_limit, ratio,
        )
        console.print(
            f"  [yellow]⚠[/yellow] {tr('send.auto_compress', used=f'{ctx_tokens:,}', limit=f'{ctx_limit:,}', pct=f'{int(ratio*100)}')}"
        )
        # Каскад: сначала инкрементальная компрессия (сжать старое, последние
        # раунды оставить дословно). Если раундов мало — полный compress.
        from commands.slash_handler import _handle_compress_incremental
        did_incremental = await _handle_compress_incremental(state)
        if not did_incremental:
            await _handle_compress(state)
        state._auto_compress_last_msg = state.session.message_count

        try:
            tg = _get_tg_bridge()
            if tg.is_running:
                tg.send(f"🗜 <b>Auto-compression</b> at {ratio:.0%} of context")
        except Exception:
            logger.debug("tg notify auto-compress failed", exc_info=True)
    except Exception as e:
        logger.error("auto-compress failed: %s", e, exc_info=True)
        console.print(f"  [red]✗ {tr('send.auto_compress_failed', error=str(e))}[/red]")


async def _resume_agent_for_background(state: InteractiveState, tg_bridge) -> bool:
    """Будит агента, когда фоновая задача завершилась во время ожидания ввода.

    Дренирует уведомления о завершённых задачах и запускает ход агента с ними
    как сообщением. Возвращает True, если ход был запущен.
    """
    from agent.loop import _format_background_notice
    from tools.background import clear_finish_event, drain_finished_results

    clear_finish_event()
    notice = _format_background_notice(drain_finished_results())
    if not notice:
        return False

    console.print()
    console.print(f"  [dim]⚙ {tr('background.autoresume')}[/dim]")

    if tg_bridge.is_running:
        try:
            tg_bridge.send("⚙ <i>background task finished — resuming…</i>")
        except Exception:
            logger.debug("tg notify bg-resume failed", exc_info=True)

    _set_activity_status(state, "working")
    state.msg_num += 1

    # Уведомление идёт в историю как пользовательский ход — агент видит его и
    # продолжает работу (loop сам умеет реагировать на bg-notice).
    state.session.add_user_message(notice, model=state.cur_model)
    try:
        _ctx = get_current_ctx()
        if _ctx is not None and getattr(_ctx, "render_store", None) is not None:
            _ctx.render_store.add_user(notice, status=build_status_line(state))
    except Exception:
        logger.debug("bg-resume render_store add_user failed", exc_info=True)

    coro = gsagent.run_agent_interactive(
        notice, model=state.cur_model, working_dir=state.workdir,
        is_continuation=True,
        session=state.session,
        mode=state.mode_state["mode"],
    )
    try:
        state.last_elapsed, _cancelled = await _run_with_interrupt(coro, state.session)
        _set_activity_status(state, "idle" if _cancelled else "done")
    except Exception as e:
        _set_activity_status(state, "idle")
        console.print(f"\n  [red]{tr('send.error_run', error=str(e))}[/red]")
    return True


def _bg_autoresume_enabled() -> bool:
    """Флаг авто-резюма агента при завершении фоновой задачи (default True)."""
    try:
        from config.settings import get as _settings_get
        return bool(_settings_get("background_autoresume", True))
    except Exception:
        return True


async def _read_user_with_tg(state: InteractiveState, status: str, tg_bridge):
    """Читает следующий ввод либо из stdin, либо из Telegram (что придёт раньше).

    Возвращает строку, _EOF, _BG_RESUME или None (Ctrl+C).
    """
    bg_resume = _bg_autoresume_enabled()
    # Если TG не запущен — простой путь
    if not tg_bridge.is_running or tg_bridge.incoming_queue is None:
        with patch_stdout():
            return await state.prompt_input.read(status_text=status, bg_resume=bg_resume)

    async def _stdin():
        with patch_stdout():
            return await state.prompt_input.read(status_text=status, bg_resume=bg_resume)

    async def _tg():
        return await tg_bridge.incoming_queue.get()

    stdin_task = asyncio.create_task(_stdin(), name="stdin-read")
    tg_task = asyncio.create_task(_tg(), name="tg-read")
    try:
        done, pending = await asyncio.wait(
            [stdin_task, tg_task], return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        for t in (stdin_task, tg_task):
            if not t.done():
                t.cancel()
        raise

    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("pending task raised on cancel", exc_info=True)

    finished = next(iter(done))
    result = finished.result()

    if finished is tg_task:
        from apis.telegram import IncomingMessage
        if isinstance(result, IncomingMessage):
            text = result.text
            # Спец-маркер действия из TG-меню — выполняем в main loop
            if text.startswith("__tg_action__:"):
                action = text.split(":", 1)[1]
                await _apply_tg_action(state, action)
                # Перечитать ввод — рекурсивно (Ctrl+C/EOF корректно прокинутся)
                new_status = build_status_line(state)
                return await _read_user_with_tg(state, new_status, tg_bridge)
            console.print()
            console.print(f"  [bold magenta]📱 TG[/bold magenta] [dim]@{escape(result.username or str(result.user_id))}:[/dim] {escape(text[:200])}")
            # Подтверждаем приём задачи (slash-команды обрабатываются bridge'ем отдельно).
            if not text.startswith("/"):
                try:
                    if tg_bridge.is_running:
                        tg_bridge.send("📨 <i>task received — working…</i>")
                except Exception:
                    logger.debug("tg ack send failed", exc_info=True)
            state._last_input_from_tg = True
            return text
        return ""
    state._last_input_from_tg = False
    return result
