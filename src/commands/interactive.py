"""Интерактивный диалог со стримингом — API-only.

Как устроен цикл
----------------
Экраном владеет ровно один `ui.shell.Shell`: он держит рамку с полем ввода и
отдаёт наверх события (`shell.submissions`). Ход агента больше **не**
блокирует ввод — его исполняет последовательная очередь
(`commands.agent_queue.AgentQueue`) в фоновом воркере, поэтому печатать и
отправлять новые сообщения можно и во время ответа.

Главный цикл здесь стал маршрутизатором: он только разбирает события из
воронки (клавиатура, Telegram, пробуждение фоновой задачей, Ctrl+C) и решает,
что поставить в очередь. Сам ход живёт в `_run_turn`, slash-команда — в
`_run_slash`.

Обработка SlashResult — в commands/slash_handler.py.
InteractiveState — в commands/interactive_state.py.
Сборка status-line — в commands/interactive_status.py.
"""

import asyncio
import contextlib
import inspect
import logging
import os
import sys
from collections.abc import Callable

import click
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markup import escape

import agent as gsagent
import config
import models as app_models
from agent import get_current_ctx
from apis.telegram import get_bridge as _get_tg_bridge
from commands.agent_queue import AgentQueue, is_immediate_slash
from commands.helpers import (
    _print_response_separator,
    _print_welcome,
    _resolve_or_exit,
    _run_with_interrupt,
    _save_termios,
    interrupt_controller,
)
from commands.interactive_state import InteractiveState
from commands.interactive_status import build_status_line
from commands.slash import _handle_slash
from commands.slash_handler import handle_slash_result
from config.i18n import t as tr
from config.themes import t
from session import Session
from ui.clipboard import cleanup_old_images
from ui.file_context import expand_at_references
from ui.overlays import paint
from ui.prompt import InputPrompt
from ui.shell import (
    SUBMIT_BG_RESUME,
    SUBMIT_EOF,
    SUBMIT_INTERRUPT,
    SUBMIT_SLASH,
    SUBMIT_TG,
    SUBMIT_USER,
    Shell,
    ensure_static_blank,
    get_shell,
)
from ui.shell import print_static as _static

logger = logging.getLogger(__name__)
console = Console()

#: Служебные «команды» очереди: приходят не от пользователя, а изнутри REPL.
#: Так резюм после фоновой задачи и действия из Telegram-меню попадают в тот же
#: строго последовательный поток и не могут наложиться на идущий ход (они меняют
#: сессию). Префикс \x00 гарантирует, что с клавиатуры такое не наберут.
_CMD_BG_RESUME = "\x00bg-resume"
_CMD_TG_ACTION = "\x00tg-action:"


def _log_task_error(task: asyncio.Task) -> None:
    """Забирает исключение у fire-and-forget задачи, чтобы оно попало в лог,
    а не в «Task exception was never retrieved» при сборке мусора."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("task %s failed: %s", task.get_name(), exc, exc_info=exc)


def _run_maybe_async(result) -> None:
    """Прокручивает корутину вне event loop (онбординг идёт до его старта)."""
    if inspect.isawaitable(result):
        asyncio.run(result)


def _start_data_cleanup() -> None:
    """Тихая фоновая очистка мусора из .data (не чаще раза в сутки).

    В фоне — тяжёлые обходы не должны задерживать старт. Любые
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


def _status_extra(state: InteractiveState) -> str:
    """Хвост статус-строки: состояние очереди ходов.

    Индикатор режима живёт в самом поле ввода (перед `❯`, см. Shell), а здесь
    только сколько сообщений ждёт своей очереди — иначе отправка во время
    ответа выглядела бы как «ничего не произошло».
    """
    queue = getattr(state, "agent_queue", None)
    if queue is not None:
        return queue.status_text()
    return ""


def _make_status_refresher(state: InteractiveState) -> Callable[[], None]:
    """Хук «после действия агента» — потокобезопасная обёртка над refresh.

    Инструменты исполняются в executor-потоке (run_in_executor), а прямое
    выполнение _refresh_status оттуда читало бы очередь параллельно с
    loop-потоком. Перекидываем refresh на loop приложения.
    """
    def _refresh() -> None:
        shell = get_shell()
        loop = getattr(shell, "_loop", None) if shell is not None else None
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            try:
                loop.call_soon_threadsafe(_refresh_status, state)
            except Exception:
                logger.debug("status refresh reschedule failed", exc_info=True)
            return
        _refresh_status(state)
    return _refresh


def _refresh_status(state: InteractiveState) -> None:
    """Обновляет верхнюю линию рамки и перевязывает ctx для Ctrl+O replay.

    Зовётся там, где прежний код печатал separator: после хода, после
    slash-команды и на каждое изменение очереди. Дополнительно вешает на ctx
    хук refresh_status — по нему loop агента обновляет панель после каждого
    инструмента, ответа и субагента.
    """
    shell = get_shell()
    if shell is not None:
        # Маркеры прогресс-бара НЕ срезаем: Shell сам разбирает их на фрагменты
        # и красит ▮ акцентом, ▯ приглушённо. Срезка делала полосу одноцветной.
        shell.set_status(build_status_line(state, extra=_status_extra(state)))
        queue = getattr(state, "agent_queue", None)
        queued = (
            queue.pending_user_texts()
            if queue is not None and queue.current_kind is not None
            else []
        )
        shell.set_queued_messages(queued)
    try:
        ctx = get_current_ctx()
        if ctx is not None:
            # ctx пересоздаётся внутри run_agent — привязки надо обновлять,
            # иначе Ctrl+O увидит ctx без prompt_input.
            ctx.prompt_input = state.prompt_input
            ctx.refresh_status = _make_status_refresher(state)
    except Exception:
        logger.debug("rebind ctx status failed", exc_info=True)


def _ctrl_o_replay(state: InteractiveState) -> None:
    """Ctrl+O: перерисовать историю раунда в раскрытом/свёрнутом виде.

    Shell зовёт это внутри `run_in_terminal`, то есть рамка уже снята с экрана
    и терминал наш. Отдельно перепечатывать статус-линию, как делал старый
    prompt, больше не нужно — рамку prompt_toolkit вернёт сам, со свежим
    статусом.
    """
    try:
        from agent.display import is_expanded_preview
        from agent.render_replay import clear_terminal, replay
        ctx = get_current_ctx()
        if ctx is None:
            return
        store = getattr(ctx, "render_store", None)
        if store is None or len(store) == 0:
            return
        next_expanded = not is_expanded_preview()
        logger.debug("ctrl+o toggle: expand=%s items=%d", next_expanded, len(store))
        clear_terminal()
        replay(store, expand=next_expanded)
        _refresh_status(state)
    except Exception:
        logger.warning("ctrl+o toggle failed", exc_info=True)


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
            console.print(f"[{t('error')}]{tr('boot.api_not_found', name=api_provider)}[/{t('error')}]")
            console.print(f"[dim]{tr('boot.add_via_api')}[/dim]")
            return
        saved_model = config.get_active_api_model() if config.get_active_api() == api_provider else ""
        if saved_model and defn.get_model_info(saved_model):
            api_model = saved_model
        else:
            api_model = defn.default_model or (defn.models[0].id if defn.models else "")
        if not api_model:
            console.print(f"[{t('error')}]{tr('boot.no_models_for', name=api_provider)}[/{t('error')}]")
            return
        config.set_active_api(api_provider)
        config.set_active_api_model(api_model)

    from commands.onboarding import _ensure_default_provider, needs_onboarding, run_onboarding
    # Онбординг идёт ДО event loop и до Shell: он выбирает язык/тему/провайдера,
    # а модель ниже резолвится уже из свежего конфига. Его точка входа
    # переезжает на async (нужны оверлеи), поэтому корутину прокручиваем своим
    # asyncio.run — Application ещё не поднят, и виджеты прозрачно падают на
    # прежний синхронный путь.
    if needs_onboarding():
        _run_maybe_async(run_onboarding())
    elif not config.get_active_api():
        _run_maybe_async(_ensure_default_provider())

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
                console.print(f"[{t('error')}]{tr('boot.session_not_found', name=resume)}[/{t('error')}]")
                return
        else:
            session = Session(working_dir=workdir)

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
                            tg_warn = f"  [{t('warning')}]⚠ Telegram: {escape(info)}[/{t('warning')}]"
                    except Exception as e:
                        tg_warn = f"  [{t('warning')}]⚠ Telegram: {escape(str(e))}[/{t('warning')}]"
                        logger.error("tg start failed: %s", e, exc_info=True)
                else:
                    tg_warn = f"[dim]{tr('boot.telegram_enabled_not_configured')}[/dim]"

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
                console.print(f"  [{t('warning')}]⚠ MCP/{_sid}:[/{t('warning')}] [dim]{escape(_err)}[/dim]")

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
                _refresh_status(state)

            # ── Shell: единственный владелец экрана на всю сессию ──
            shell = Shell(working_dir=workdir)
            shell.mode = state.mode_state.get("mode", "agent")
            shell.on_mode_toggle = _toggle_mode
            shell.on_ctrl_o = lambda: _ctrl_o_replay(state)

            state.prompt_input = InputPrompt(
                working_dir=workdir, on_mode_toggle=_toggle_mode, shell=shell,
            )
            state.prompt_input.session = state.session
            state.prompt_input.status_provider = lambda: build_status_line(state)
            _set_activity_status(state, "idle")
            # Привязываем asyncio-loop к фоновым задачам: завершившаяся в фоне
            # задача сможет разбудить агента (авто-резюм) через _bg_pump.
            try:
                from tools.background import register_event_loop
                register_event_loop(asyncio.get_running_loop())
            except Exception:
                logger.debug("background event-loop register failed", exc_info=True)

            queue = AgentQueue(
                run_turn=lambda texts: _run_turn(state, texts, tg_bridge),
                run_slash=lambda text: _run_slash(state, text),
                on_change=lambda: _refresh_status(state),
            )
            state.agent_queue = queue
            shell.on_edit_queued = queue.pop_all_users_for_edit

            # patch_stdout — на весь цикл: аварийный console.print из старого кода
            # вклинивается над рамкой, а не рвёт её. Slash-команды дополнительно
            # перехватывают свой вывод в `_run_slash` и показывают его как
            # динамическое notice. raw=True обязателен: без него prompt_toolkit заменяет ESC на
            # "?" и цвета печатаются текстом ("?[38;2;…m").
            # Снимок termios — пока терминал в cooked-режиме: Application тут же
            # переведёт его в raw и будет держать так всю сессию, а аварийный
            # выход по третьему Ctrl+C должен вернуть исходное состояние.
            _save_termios()

            with patch_stdout(raw=True):
                app_task = shell.start()
                # Статус ставим только после start(): до него Shell ещё не
                # синглтон, и set_status ушёл бы в никуда — рамка стартовала бы
                # с пустой верхней линией.
                _refresh_status(state)
                pumps = [
                    asyncio.create_task(_tg_pump(state, tg_bridge), name="tg-pump"),
                    asyncio.create_task(_bg_pump(state), name="bg-pump"),
                ]
                queue.start()
                immediate: asyncio.Task | None = None
                try:
                    while True:
                        kind, text = await shell.submissions.get()

                        if kind == SUBMIT_EOF:
                            break

                        if kind == SUBMIT_INTERRUPT:
                            # Ctrl+C приходит клавишей (терминал в raw-режиме),
                            # поэтому эскалацию двигаем руками.
                            interrupt_controller().escalate()
                            continue

                        if kind == SUBMIT_BG_RESUME:
                            # Через очередь, а не напрямую: резюм не должен
                            # наложиться на идущий ход. Дубли не копим.
                            if not any(i.text == _CMD_BG_RESUME for i in queue.pending):
                                queue.submit_slash(_CMD_BG_RESUME)
                            continue

                        if kind == SUBMIT_TG and text.startswith("/"):
                            # Команды из Telegram исполняются локально, как и раньше.
                            kind = SUBMIT_SLASH

                        if kind == SUBMIT_SLASH:
                            if is_immediate_slash(text) and (
                                immediate is None or immediate.done()
                            ):
                                # Команды-виджеты идут мимо очереди, но строго по
                                # одной: два оверлея одновременно Shell не держит.
                                immediate = asyncio.create_task(
                                    _run_slash(state, text), name="slash-now")
                                immediate.add_done_callback(_log_task_error)
                            else:
                                queue.submit_slash(text)
                            continue

                        if kind in (SUBMIT_USER, SUBMIT_TG):
                            if kind == SUBMIT_USER:
                                # Маркеры многострочных вставок раскрываем здесь:
                                # буфером владеет Shell, хука истории больше нет.
                                text = state.prompt_input.expand_submitted(text)
                                _mirror_user_to_tg(text, tg_bridge)
                            _set_activity_status(state, "working")
                            queue.submit_user(text)
                            continue
                finally:
                    for task in pumps:
                        task.cancel()
                    if immediate is not None:
                        immediate.cancel()
                    for task in (*pumps, immediate):
                        if task is not None:
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await task
                    await queue.stop()
                    await _stop_recap_tasks(state)
                    await _stop_round_compress_task(state)
                    await shell.stop()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.wait_for(app_task, timeout=3)
                    _cmd = paint(f"python {sys.argv[0]} cli --resume {state.session.id}",
                                 "accent", bold=True)
                    shell.print_exit_notice(tr('common.resume_hint', cmd=_cmd))

        finally:
            from session import storage as _storage
            _storage.save(state.session)
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
                tg = _get_tg_bridge()
                if tg.is_running:
                    tg.send("🔴 <b>necli-api</b> stopped")
                    await tg.stop()
            except Exception:
                logger.debug("tg stop failed", exc_info=True)

    asyncio.run(_run())


def _mirror_user_to_tg(text: str, tg_bridge) -> None:
    """Зеркалим в TG только ввод из терминала (из TG он уже виден в чате)."""
    try:
        if tg_bridge.is_running:
            from agent.telegram_handler import TelegramEventHandler
            TelegramEventHandler(None).mirror_user(text)
    except Exception:
        logger.debug("tg mirror_user failed", exc_info=True)


# ────────────────────────────── ход агента ──────────────────────────────────
async def _run_turn(state: InteractiveState, texts: list[str], tg_bridge) -> None:
    """Один ход агента. `texts` — батч реплик, накопившихся пока агент работал.

    Эхо всех реплик батча печатается ЗДЕСЬ, до первого кадра стрима. Если
    печатать его в момент нажатия Enter, оно вклинится в середину ответа на
    предыдущее сообщение — воркер и главный цикл работают одновременно.
    """
    # Долгий API-запрос автопруна выполняется отдельно от очереди. Если его
    # summary уже готов, применяем его здесь — до добавления нового user и до
    # запуска следующего API-хода. Сама операция занимает только локальную
    # замену префикса истории и не задерживает ввод на время генерации summary.
    await _apply_pending_round_compress(state)

    status = build_status_line(state)
    for text in texts:
        state.prompt_input.echo_submitted(text)
    # Отдаём управление loop'у, чтобы эхо реально нарисовалось СЕЙЧАС.
    # Печать идёт через run_in_terminal, то есть отложенной задачей; без этой
    # уступки loop не крутится, и пользователь видит паузу в секунду между
    # Enter и появлением своей реплики.
    await asyncio.sleep(0)

    # Для агента батч — одно сообщение: параллельные ходы недопустимы, а
    # склейка сохраняет порядок реплик.
    user = "\n".join(texts)

    _set_activity_status(state, "working")

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

    # Панель над вводом обновляется на старте хода (счётчик сообщений уже
    # вырос), а главное — здесь на ctx вешается хук refresh_status, по которому
    # loop агента обновляет панель после каждого инструмента и ответа. До этого
    # места ctx может ещё не существовать (первый ход), и привязка потерялась
    # бы, оставив промежуточные обновления молчаливыми no-op.
    _refresh_status(state)

    agent_message = user
    # Маппинг [imageN] → реальный путь, чтобы агент мог открыть
    # вставленные картинки как файлы через инструменты (read и др.).
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
        _static(f"[dim]📄 {tr('send.context_files', files=files_str)}[/dim]")
        agent_message = file_context_block + "\n\n" + agent_message

    # Полное описание mode/think — в системном промте (пересобирается
    # каждый запрос). В поток шлём ТОЛЬКО короткий one-shot сигнал при
    # переключении, чтобы модель явно заметила смену в середине диалога.
    if state.mode_state["changed"]:
        from system_prompt import (
            MODE_SWITCH_TO_AGENT,
            MODE_SWITCH_TO_PLANNING,
            MODE_SWITCH_TO_SWARM,
        )
        if state.mode_state["mode"] == "planning":
            mode_notice = MODE_SWITCH_TO_PLANNING
        elif state.mode_state["mode"] == "swarm":
            mode_notice = MODE_SWITCH_TO_SWARM
        else:
            mode_notice = MODE_SWITCH_TO_AGENT
        agent_message = mode_notice + "\n\n" + agent_message
        state.mode_state["changed"] = False

    if state.think_changed:
        from system_prompt import THINK_SWITCH_OFF, THINK_SWITCH_ON
        notice = THINK_SWITCH_ON if state.think_enabled else THINK_SWITCH_OFF
        agent_message = notice + "\n\n" + agent_message
        state.think_changed = False

    history_for_msg = None
    if state.pending_context:
        history_for_msg = state.pending_context
        state.pending_context = None

    is_cont = state.msg_num > 1

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
        _static(f"\n  [{t('error')}]{tr('send.error_run', error=str(e))}[/{t('error')}]")

    # Если фото не прошло (модель без поддержки изображений или файл
    # повреждён) — убираем его из истории сессии, чтобы после /resume оно не
    # прикрепилось снова и не уронило следующий запрос.
    if message_images:
        try:
            from apis.agent_adapter import get_api_session
            api_sess = get_api_session()
            if api_sess is not None and getattr(api_sess, "image_fallback", False):
                user_message.attachments = []
                logger.info("image fallback: photo removed from session history")
        except Exception:
            logger.debug("image fallback history cleanup failed", exc_info=True)

    _print_response_separator()

    _schedule_recap_output(state)

    # ── Авто-компрессия при ≥90% контекстного лимита ──
    await _maybe_auto_compress(state)

    # ── Autoprune round-compression (режим без кэша): каждые N раундов / порог токенов ──
    await _maybe_round_compress(state)

    # Очень быстрый провайдер мог успеть вернуть summary до конца этого хода.
    # Применяем его сейчас; обычно эта ветка пустая, а завершившаяся позже
    # задача применит результат сама, когда очередь станет idle.
    await _apply_pending_round_compress(state)

    # ── Отложенные запросы из Telegram-меню ──
    if getattr(state, "_tg_compress_requested", False):
        state._tg_compress_requested = False
        await _handle_tg_compress(state)

    _refresh_status(state)


async def _run_slash(state: InteractiveState, text: str) -> None:
    """Выполняет slash-команду (и служебные команды очереди)."""
    if text == _CMD_BG_RESUME:
        if await _resume_agent_for_background(state):
            _print_response_separator()
            _schedule_recap_output(state)
        _refresh_status(state)
        return

    if text.startswith(_CMD_TG_ACTION):
        await _apply_tg_action(state, text[len(_CMD_TG_ACTION):])
        _refresh_status(state)
        return

    # Captureим вывод slash-команды, сохраняем в render_store как raw_console
    # item — чтобы Ctrl+O replay показал команды. Интерактивные виджеты в
    # capture больше не попадают: их рисует Shell, а не эта Console.
    with console.capture() as _cap:
        act = await _handle_slash(text, state.cur_model, state.session, state.last_elapsed)
        await handle_slash_result(act, state)
    _captured = _cap.get()
    # Никакие служебные сообщения slash-команд не остаются в scrollback.
    # Короткий результат живёт в динамической строке статуса.
    try:
        _ctx = get_current_ctx()
        if _ctx is not None and getattr(_ctx, "render_store", None) is not None:
            _ctx.render_store.add("raw_console", {
                "command": text,
                "output": _captured or "",
            })
    except Exception:
        logger.debug("store slash raw_console failed", exc_info=True)
    _refresh_status(state)


# ──────────────────────── насосы внешних событий ────────────────────────────
async def _tg_pump(state: InteractiveState, tg_bridge) -> None:
    """Переливает входящие из Telegram в ту же воронку, что и клавиатуру.

    Раньше stdin и TG читались двумя конкурирующими задачами через
    `asyncio.wait`; теперь ввод всегда открыт, поэтому TG просто кладёт
    сообщение в `shell.submissions`, а очередь решает, когда его исполнить.
    """
    from apis.telegram import IncomingMessage

    while True:
        queue = tg_bridge.incoming_queue if tg_bridge.is_running else None
        if queue is None:
            await asyncio.sleep(1.0)
            continue
        try:
            # Таймаут — чтобы заметить перезапуск бриджа через /tg: он заводит
            # новую очередь, и висеть на старой было бы бессмысленно.
            msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        except (TimeoutError, asyncio.TimeoutError):
            continue
        if not isinstance(msg, IncomingMessage):
            continue
        text = (msg.text or "").strip()
        if not text:
            continue

        if text.startswith("__tg_action__:"):
            # Действие из TG-меню меняет сессию — только через очередь.
            queue_obj = getattr(state, "agent_queue", None)
            if queue_obj is not None:
                queue_obj.submit_slash(_CMD_TG_ACTION + text.split(":", 1)[1])
            continue

        shell = get_shell()
        if shell is None:
            continue
        _static(
            f"\n  [bold {t('magenta')}]📱 TG[/bold {t('magenta')}]"
            f" [dim]@{escape(msg.username or str(msg.user_id))}:[/dim]"
            f" {escape(text[:200])}"
        )
        # Подтверждаем приём задачи (slash-команды обрабатываются bridge'ем отдельно).
        if not text.startswith("/"):
            try:
                tg_bridge.send("📨 <i>task received — working…</i>")
            except Exception:
                logger.debug("tg ack send failed", exc_info=True)
        shell.submissions.put_nowait((SUBMIT_TG, text))


async def _bg_pump(state: InteractiveState) -> None:
    """Будит агента, когда фоновая задача завершилась.

    Правило «не мешать тому, кто печатает» сохранено: если в поле ввода есть
    текст — пробуждение пропускаем, результат приедет вместе со следующим
    ходом (loop сам подмешивает уведомления в ближайший раунд).
    """
    from tools.background import clear_finish_event, get_finish_event, has_pending_finished

    while True:
        shell = get_shell()
        event = get_finish_event()
        if shell is None or event is None:
            await asyncio.sleep(0.5)
            continue
        await event.wait()
        clear_finish_event()
        if not _bg_autoresume_enabled() or not has_pending_finished():
            continue
        if (shell.input_buffer.text or "").strip():
            continue
        shell.submissions.put_nowait((SUBMIT_BG_RESUME, None))


async def _apply_tg_action(state: InteractiveState, action: str) -> None:
    """Выполняет отложенное TG-действие в контексте очереди ходов."""
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
_RECAP_EVERY = 10
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
    После ответа задача передаётся в `_schedule_recap_output`: очередь агента
    её больше не ждёт, а готовый результат печатается отдельной задачей.
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


def _schedule_recap_output(state: InteractiveState) -> None:
    """Отвязать recap от хода агента и напечатать его по готовности.

    Важно не делать ``await`` в `_run_turn`: этот coroutine исполняет worker
    AgentQueue, поэтому ожидание вспомогательного API-запроса удерживало всю
    очередь пользовательских сообщений после уже законченного ответа.
    """
    generation_task = state.recap_task
    if generation_task is None:
        return
    state.recap_task = None

    async def _deliver() -> None:
        try:
            text = await generation_task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("recap await failed", exc_info=True)
            return
        if not text or not text.strip():
            return
        ensure_static_blank()
        _static(f"[italic grey62]📋 {escape(text.strip())}[/italic grey62]")
        ensure_static_blank()

    try:
        output_task = asyncio.create_task(_deliver(), name="recap-output")
    except Exception:
        logger.debug("recap output task launch failed", exc_info=True)
        generation_task.cancel()
        return
    # Храним сильные ссылки на ОБЕ задачи. Если delivery отменить до её первого
    # тика, asyncio ещё не успеет войти в `await generation_task` и не передаст
    # отмену API-запросу автоматически.
    state.recap_background_tasks.update((generation_task, output_task))
    generation_task.add_done_callback(state.recap_background_tasks.discard)
    output_task.add_done_callback(state.recap_background_tasks.discard)
    output_task.add_done_callback(_log_task_error)


async def _stop_recap_tasks(state: InteractiveState) -> None:
    """Отменить генерацию/доставку recap при завершении интерактивной сессии."""
    tasks = set(state.recap_background_tasks)
    if state.recap_task is not None:
        tasks.add(state.recap_task)
    state.recap_task = None
    state.recap_background_tasks.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _stop_round_compress_task(state: InteractiveState) -> None:
    """Не оставлять запрос автопруна жить после закрытия интерактивной сессии."""
    task = getattr(state, "_round_compress_task", None)
    state._round_compress_task = None
    state._round_compress_pending = None
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


def _autoprune_active() -> bool:
    """True когда autoprune режим активен: у активного провайдера ВЫКЛЮЧЕН prompt cache.

    Ползунок кэша в настройках провайдера — мастер-переключатель: cache ON →
    autoprune off (поведение прежнее), cache OFF → autoprune on.
    """
    try:
        from apis.agent_adapter import get_api_session
        sess = get_api_session()
        if sess is None or sess.llm is None:
            return False
        return not sess.llm._supports_anthropic_cache_control()
    except Exception:
        return False


async def _maybe_round_compress(state: InteractiveState) -> None:
    """Autoprune round-compression: сжатие истории каждые N раундов / при пороге токенов.

    Триггеры (OR, только после завершённого раунда):
      - число раундов (user-сообщений) кратно N (autoprune_compress_every_rounds, дефолт 10);
      - context_tokens >= порога (autoprune_compress_at_tokens, дефолт 200k).
    Сжимаем ВСЕ раунды кроме последнего (последний остаётся дословно) → в истории
    получается summary-контекст + сразу же последний user-раунд. После сжатия
    round/token-эвристики сбрасываются (гейт на одно и то же число раундов).
    """
    if not _autoprune_active():
        return
    from config.settings import get as _settings_get
    if not _settings_get("autoprune_round_compression", True):
        return
    every = int(_settings_get("autoprune_compress_every_rounds", 10) or 10)
    at_tokens = int(_settings_get("autoprune_compress_at_tokens", 200_000) or 200_000)
    if every <= 0:
        every = 10

    sess = state.session
    rounds = sess.message_count  # число user-сообщений = число раундов
    if rounds < 2:
        return
    # Не сжимать повторно на том же числе раундов (после сжатия хвост короче).
    if rounds == getattr(state, "_round_compress_rounds", 0):
        return
    hit_rounds = (rounds % every) == 0
    hit_tokens = sess.context_tokens >= at_tokens
    if not (hit_rounds or hit_tokens):
        return
    # Не запускаем два summary одновременно. Новый триггер проверится после
    # следующего раунда, когда текущий результат уже будет применён.
    task = getattr(state, "_round_compress_task", None)
    if task is not None and not task.done():
        return
    if getattr(state, "_round_compress_pending", None) is not None:
        return

    # Сжимаем всё, кроме последнего раунда (последний остаётся дословно).
    tail_index = sess.tail_split_index(1)
    if tail_index <= 0:
        return

    from system_prompt import ROUND_COMPRESS_PROMPT
    history_text = sess.build_compress_text(upto_index=tail_index)
    if not history_text.strip():
        return

    # Запоминаем обработанное число раундов ДО запуска, чтобы один и тот же
    # порог не создавал повторные фоновые запросы.
    state._round_compress_rounds = rounds
    compress_prompt = ROUND_COMPRESS_PROMPT + "\n\n" + history_text
    _static(f"[dim]⚙ {tr('autoprune.round_compress_start', n=rounds)}[/dim]")
    state._round_compress_task = asyncio.create_task(
        _generate_round_compress(
            state,
            session_id=sess.id,
            rounds=rounds,
            tail_index=tail_index,
            history_text=history_text,
            compress_prompt=compress_prompt,
            model=state.cur_model,
        ),
        name=f"autoprune-compress-{rounds}",
    )


async def _generate_round_compress(
    state: InteractiveState,
    *,
    session_id: str,
    rounds: int,
    tail_index: int,
    history_text: str,
    compress_prompt: str,
    model: str,
) -> None:
    """Сгенерировать summary, не занимая последовательную очередь агента."""
    try:
        from apis.agent_adapter import api_compress_history

        compressed = (await api_compress_history(compress_prompt)).strip()
        if not compressed:
            logger.warning("round compress: empty summary, skipping")
            return
        # Никаких мутаций Session во время активного хода: готовый результат
        # лежит отдельно и применяется только на безопасной границе.
        state._round_compress_pending = {
            "session_id": session_id,
            "rounds": rounds,
            "tail_index": tail_index,
            "history_text": history_text,
            "compressed": compressed,
            "model": model,
        }
        # Если очередь уже простаивает, следующего хода для commit может не
        # быть. Даём worker один тик завершить текущий _run_turn и применяем
        # summary сами только после перехода в idle.
        await asyncio.sleep(0)
        queue = getattr(state, "agent_queue", None)
        if queue is None or not queue.busy:
            await _apply_pending_round_compress(state)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("round compress failed: %s", e, exc_info=True)
        _static(
            f"  [{t('error')}]✗ "
            f"{tr('autoprune.round_compress_failed', error=str(e))}"
            f"[/{t('error')}]"
        )
    finally:
        current = asyncio.current_task()
        if getattr(state, "_round_compress_task", None) is current:
            state._round_compress_task = None


async def _apply_pending_round_compress(state: InteractiveState) -> bool:
    """Быстро применить готовый summary, сохранив добавленные после него ходы."""
    pending = getattr(state, "_round_compress_pending", None)
    if not pending:
        return False

    sess = state.session
    tail_index = int(pending["tail_index"])
    same_source = (
        sess.id == pending["session_id"]
        and tail_index > 0
        and len(sess.messages) >= tail_index
        and sess.build_compress_text(upto_index=tail_index) == pending["history_text"]
    )
    # /new, ручной /compress или другая перестройка истории могли заменить
    # исходный префикс. В таком случае старый summary применять опасно.
    if not same_source:
        state._round_compress_pending = None
        logger.info("round compress: source history changed, dropping stale summary")
        return False

    state._round_compress_pending = None
    try:
        rounds = int(pending["rounds"])
        from session import storage as _storage

        sess.compress_reset_partial(
            str(pending["compressed"]),
            tail_index,
            model=str(pending["model"]),
        )
        _storage.save(sess)
        from tools.file_ops.read import clear_read_cache

        clear_read_cache()
        from apis.agent_adapter import api_new_chat, restore_api_session_history

        await api_new_chat()
        restore_api_session_history(sess)
        state.pending_context = None
        logger.info(
            "round compress applied: rounds=%s tokens=%s",
            rounds,
            sess.context_tokens,
        )
        _static(
            f"[{t('success')}]✓[/{t('success')}] "
            f"{tr('autoprune.round_compress_done', rounds=rounds)}"
        )
        _static("")
        return True
    except Exception as e:
        logger.error("round compress apply failed: %s", e, exc_info=True)
        _static(
            f"  [{t('error')}]✗ "
            f"{tr('autoprune.round_compress_failed', error=str(e))}"
            f"[/{t('error')}]"
        )
        return False


async def _maybe_auto_compress(state: InteractiveState) -> None:
    """Запустить safety-сжатие ≥90% в фоне, не занимая очередь агента."""
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
        task = getattr(state, "_round_compress_task", None)
        if task is not None and not task.done():
            return
        if getattr(state, "_round_compress_pending", None) is not None:
            return
        logger.info(
            "auto-compress trigger: session=%s ctx=%s/%s (%.0f%%)",
            state.session.id[:16], ctx_tokens, ctx_limit, ratio * 100,
        )
        _static(
            f"  [{t('warning')}]⚠[/{t('warning')}] {tr('send.auto_compress', used=f'{ctx_tokens:,}', limit=f'{ctx_limit:,}', pct=f'{int(ratio*100)}')}"
        )

        sess = state.session
        # Обычно оставляем последние четыре раунда дословно. Если история ещё
        # короткая, summary строится по всему текущему снимку; сообщения,
        # пришедшие во время генерации, всё равно останутся хвостом при commit.
        tail_index = sess.tail_split_index(4)
        if tail_index <= 0:
            tail_index = len(sess.messages)
        if tail_index <= 0:
            return
        history_text = sess.build_compress_text(upto_index=tail_index)
        if not history_text.strip():
            return

        from system_prompt import COMPRESS_PROMPT

        rounds = sess.message_count
        state._round_compress_rounds = rounds
        state._auto_compress_last_msg = state.session.message_count
        state._round_compress_task = asyncio.create_task(
            _generate_round_compress(
                state,
                session_id=sess.id,
                rounds=rounds,
                tail_index=tail_index,
                history_text=history_text,
                compress_prompt=COMPRESS_PROMPT + history_text,
                model=state.cur_model,
            ),
            name=f"autoprune-safety-compress-{rounds}",
        )

        try:
            tg = _get_tg_bridge()
            if tg.is_running:
                tg.send(f"🗜 <b>Auto-compression</b> at {ratio:.0%} of context")
        except Exception:
            logger.debug("tg notify auto-compress failed", exc_info=True)
    except Exception as e:
        logger.error("auto-compress failed: %s", e, exc_info=True)
        _static(f"  [{t('error')}]✗ {tr('send.auto_compress_failed', error=str(e))}[/{t('error')}]")


async def _resume_agent_for_background(state: InteractiveState) -> bool:
    """Будит агента, когда фоновая задача завершилась.

    Дренирует уведомления о завершённых задачах и запускает ход агента с ними
    как сообщением. Возвращает True, если ход был запущен.
    """
    from agent.loop import _format_background_notice
    from tools.background import clear_finish_event, drain_finished_results

    clear_finish_event()
    notice = _format_background_notice(drain_finished_results())
    if not notice:
        return False

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
        background_resume=True,
    )
    try:
        state.last_elapsed, _cancelled = await _run_with_interrupt(coro, state.session)
        _set_activity_status(state, "idle" if _cancelled else "done")
    except Exception as e:
        _set_activity_status(state, "idle")
        _static(f"\n  [{t('error')}]{tr('send.error_run', error=str(e))}[/{t('error')}]")
    return True


def _bg_autoresume_enabled() -> bool:
    """Флаг авто-резюма агента при завершении фоновой задачи (default True)."""
    try:
        from config.settings import get as _settings_get
        return bool(_settings_get("background_autoresume", True))
    except Exception:
        return True
