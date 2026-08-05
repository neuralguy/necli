import asyncio
import subprocess
from contextlib import contextmanager

from rich.console import Console

import agent as gsagent
import config
import session.storage as storage
from commands.helpers import (
    _print_response_separator,
    _run_with_interrupt,
)
from commands.interactive_state import InteractiveState
from commands.slash import SlashResult
from config.i18n import t as tr
from config.themes import t
from logger import logger
from session import Session
from skills import reset_active_skills
from tools._paths import set_working_dir as _set_wd

console = Console()


@contextmanager
def _busy(label: str):
    """Спиннер «идёт работа» в динамической зоне Shell.

    `console.status` — это `rich.live.Live`: он двигал бы курсор в терминале,
    которым владеет Application, и рвал бы рамку. Динамическая зона
    перерисовывается самим Application, а Rich-спиннер анимируется от его
    тикера. Без Shell (headless) остаётся прежний путь.
    """
    from ui.shell import get_shell
    shell = get_shell()
    if shell is None:
        with console.status(f"[bold {t('info')}]{label}[/bold {t('info')}]", spinner="dots"):
            yield
        return
    from rich.spinner import Spinner
    spinner = Spinner("dots", text=label, style=f"bold {t('info')}")

    def _busy_provider():
        # Callable-обёртка, чтобы shell считал зону «анимируемой» и крутил
        # тайкер сам (иначе кадры идут только от ввода с клавиатуры).
        return spinner

    shell.set_dynamic("busy", _busy_provider)
    try:
        yield
    finally:
        shell.clear_dynamic("busy")


async def handle_slash_result(act: SlashResult, state: InteractiveState) -> bool:
    if act.switch_session:
        await _handle_switch_session(act, state)
        return True

    if act.change_dir:
        _handle_change_dir(act, state)
        return True

    if act.do_compress:
        await _handle_compress(state)
        return True

    if act.do_commit:
        _handle_commit(act, state)
        return True

    if act.do_new:
        await _handle_new_chat(state)
        return True

    if act.do_branch:
        await _handle_branch(state)
        return True

    if act.toggle_think:
        _handle_toggle_think(state)
        return True

    if act.toggle_tool_format:
        await _handle_toggle_tool_format(state)
        return True

    if act.do_reflect:
        await _handle_reflect(state)
        return True

    if act.switch_api is not None:
        await _handle_switch_api(act, state)

    if act.tg_toggle is not None:
        await _handle_tg_toggle(act.tg_toggle, state)

    return True


async def _handle_tg_toggle(enable: bool, state: InteractiveState) -> None:
    """Запускает/останавливает Telegram-бридж на лету (без перезапуска CLI)."""
    from apis.telegram import get_bridge
    bridge = get_bridge()

    if enable:
        if bridge.is_running:
            return
        token = config.get_telegram_bot_token()
        chat_id = config.get_telegram_chat_id()
        if not token or not chat_id:
            return
        try:
            ok = (await bridge.start(token, int(chat_id)))[0]
        except Exception as e:
            logger.error("tg toggle start failed: %s", e, exc_info=True)
            return
        if ok:
            from agent.tg_menu import _build_reply_keyboard, register_tg_menu
            register_tg_menu(state)
            bridge.send(
                f"🟢 <b>necli-api</b> bridge enabled\n"
                f"model: <code>{state.cur_model}</code>\n\n"
                f"Controls: /menu",
                reply_markup=_build_reply_keyboard(),
            )
    else:
        if not bridge.is_running:
            return
        try:
            bridge.send("🔴 <b>necli-api</b> bridge disabled")
            await bridge.stop()
        except Exception as e:
            logger.error("tg toggle stop failed: %s", e, exc_info=True)


async def _handle_switch_session(act: SlashResult, state: InteractiveState) -> None:
    sid = act.switch_session
    logger.info("switch_session: → {}", sid[:16])
    new_session = storage.load(sid)
    if not new_session:
        logger.warning("switch_session: not found {}", sid)
        return

    state.save_session()
    state.session = new_session

    from apis.agent_adapter import restore_api_session_history
    restore_api_session_history(state.session)
    state.pending_context = None
    state.msg_num = state.session.message_count
    try:
        from agent.render_replay import print_session_history
        print_session_history(state.session, max_messages=20)
    except Exception:
        logger.debug("print_session_history failed", exc_info=True)


def _handle_change_dir(act: SlashResult, state: InteractiveState) -> None:
    new_dir = act.change_dir
    state.workdir = new_dir
    _set_wd(new_dir)
    state.prompt_input.set_working_dir(new_dir)

    _cd_parts = []
    try:
        _tree_r = subprocess.run(
            ["tree", "-L", "2", "--dirsfirst", "-I",
             "__pycache__|node_modules|.venv|venv|.mypy_cache|.pytest_cache|.ruff_cache|dist|build|.egg-info|.tox|.nox|.cache|.idea|.vscode|.git"],
            capture_output=True, text=True,
            timeout=10, cwd=new_dir,
        )
        if _tree_r.returncode == 0 and _tree_r.stdout.strip():
            _cd_parts.append(f"$ tree -L 2\n{_tree_r.stdout.strip()}")
    except Exception as e:
        logger.debug("cd tree snapshot failed: {}", e)

    _cd_context = (
        f"User changed working directory to: {new_dir}\n\n"
        + "\n\n".join(_cd_parts)
    )
    state.pending_context = [{"role": "system", "content": _cd_context}]


async def _handle_compress(state: InteractiveState) -> None:
    logger.info(
        "compress: session={} msg_count={}",
        state.session.id[:16], state.session.message_count,
    )
    history_text = state.session.build_compress_text()
    if not history_text.strip():
        return

    from system_prompt import COMPRESS_PROMPT
    compress_prompt = COMPRESS_PROMPT + history_text

    from apis.agent_adapter import (
        api_compress_history,
        api_new_chat,
        get_api_session,
    )
    try:
        with _busy(tr('sh.compressing')):
            compressed = await api_compress_history(compress_prompt)

        compressed = compressed.strip()
        if not compressed:
            return

        state.session.compress_reset(compressed, model=state.cur_model)
        storage.save(state.session)

        from tools.file_ops.read import clear_read_cache
        clear_read_cache()

        await api_new_chat()
        api_sess = get_api_session()
        if api_sess is not None:
            api_sess.add_system(compressed, compressed=True)

        state.pending_context = None
        state.msg_num = 0
    except Exception as e:
        logger.error("compress failed: {}", e, exc_info=True)


_KEEP_RECENT_ROUNDS = 4


async def _handle_compress_incremental(state: InteractiveState) -> bool:
    """Каскадная авто-компрессия: сжать только СТАРУЮ часть истории, последние
    _KEEP_RECENT_ROUNDS раундов оставить дословно.

    Возвращает True если что-то сжали. Если раундов мало (нечего сжимать
    инкрементально) — возвращает False, вызывающий код делает полный compress.
    """
    sess = state.session
    tail_index = sess.tail_split_index(_KEEP_RECENT_ROUNDS)
    if tail_index <= 0:
        return False

    history_text = sess.build_compress_text(upto_index=tail_index)
    if not history_text.strip():
        return False

    from system_prompt import COMPRESS_PROMPT
    compress_prompt = COMPRESS_PROMPT + history_text

    from apis.agent_adapter import (
        api_compress_history,
        api_new_chat,
        restore_api_session_history,
    )
    with _busy(tr('sh.compressing')):
        compressed = await api_compress_history(compress_prompt)
    compressed = compressed.strip()
    if not compressed:
        return False

    n = sess.compress_reset_partial(compressed, tail_index, model=state.cur_model)
    storage.save(sess)

    from tools.file_ops.read import clear_read_cache
    clear_read_cache()

    # Пересобрать API-сессию из обновлённой necli-истории (summary + хвост).
    await api_new_chat()
    restore_api_session_history(sess)

    state.pending_context = None
    state.msg_num = sess.message_count
    logger.info("incremental compress: {} rounds compressed, tail kept", n)
    return True



_BG_COMMIT_TASKS: set = set()


def _handle_commit(act: SlashResult, state: InteractiveState) -> None:
    """Запускает фоновый commit-агент. Не блокирует ввод — пользователь может
    параллельно давать новые задачи основному агенту."""
    api_id = config.get_active_api()
    model_id = config.get_active_api_model() or ""
    if not api_id:
        return

    workdir = state.workdir
    hint = act.commit_hint or ""
    logger.info("commit-agent dispatch: api=%s model=%s wd=%s", api_id, model_id, workdir)

    from agent.commit_agent import run_commit_agent

    async def _runner():
        return await run_commit_agent(api_id, model_id, workdir, hint)

    task = asyncio.ensure_future(_runner())
    _BG_COMMIT_TASKS.add(task)

    def _done(t: asyncio.Task) -> None:
        _BG_COMMIT_TASKS.discard(t)
        try:
            text = (t.result() or "").strip()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("commit-agent failed: %s", e, exc_info=True)
            return
        logger.info("commit-agent done: %s", text.replace("\n", " ")[:200])

    task.add_done_callback(_done)


async def _handle_new_chat(state: InteractiveState) -> None:
    logger.info("new_chat (api mode)")
    old_sid = state.session.id if state.session else None
    state.save_session()
    from apis.agent_adapter import api_new_chat
    await api_new_chat()
    reset_active_skills()
    from config.permissions import reset_session as reset_permissions_session
    reset_permissions_session()
    from tools.file_ops.read import clear_read_cache
    if old_sid:
        clear_read_cache(old_sid)
    state.session = Session(working_dir=state.workdir)
    state.msg_num = 0
    state.pending_context = None
    state.prompt_input.clear_images()
    try:
        from ui.terminal_title import set_session_terminal_title
        set_session_terminal_title(state.session)
    except Exception:
        logger.debug("new chat terminal title update failed", exc_info=True)


async def _handle_branch(state: InteractiveState) -> None:
    """Создаёт новую сессию-форк с копией текущей истории.

    Текущая сессия сохраняется как есть; работа продолжается в новой
    сессии, чья история — независимая копия сообщений текущей.
    """
    from session.message import Message

    old = state.session
    state.save_session()
    logger.info("branch: from {} ({} msgs)", old.id[:16], len(old.messages))

    new_session = Session(working_dir=old.working_dir)
    new_session.messages = [Message.from_dict(m.to_dict()) for m in old.messages]
    new_session.title = old.title
    if old.messages:
        first_user = next((m.content for m in old.messages if m.role == "user"), "")
        if first_user:
            new_session._rename_for_first_message(first_user)
    storage.save(new_session)

    state.session = new_session
    state.msg_num = new_session.message_count
    state.pending_context = None
    try:
        from ui.terminal_title import set_session_terminal_title
        set_session_terminal_title(state.session)
    except Exception:
        logger.debug("branch terminal title update failed", exc_info=True)

    from apis.agent_adapter import api_new_chat, restore_api_session_history
    await api_new_chat()
    restore_api_session_history(new_session)


def _handle_toggle_think(state: InteractiveState) -> None:
    """Переключает think-флаг (ортогонален mode). Сохраняется между сессиями."""
    state.think_enabled = not state.think_enabled
    state.think_changed = True
    config.set_value("think_enabled", state.think_enabled)


async def _handle_toggle_tool_format(state: InteractiveState) -> None:
    """Переключает глобальный force-native флаг для tool calls.

    True  → все API-запросы принудительно используют native function calling.
    False → все API-запросы используют fenced text-блоки.

    После смены формата ПЕРЕсобираем API-историю из necli-сессии под новый
    формат: restore_api_session_history сериализует assistant/tool-сообщения
    по api_sess.use_native_tools (native → fenced :::call парсятся обратно в
    native tool_calls+ToolMessage; fenced → tool_calls не восстанавливаются).
    Без этого в api_sess.messages оставались сообщения В СТАРОМ формате, и
    модель продолжала имитировать их, игнорируя свежий системный промт —
    «починить» помогал только перезапуск CLI (пустая история).
    """
    current = bool(config.get("tool_format_force_native", True))
    new_val = not current
    config.set_value("tool_format_force_native", new_val)

    from apis.agent_adapter import get_api_session, restore_api_session_history
    if get_api_session() is not None:
        restore_api_session_history(state.session)
        state.pending_context = None



async def _handle_reflect(state: InteractiveState) -> None:
    from system_prompt import REFLECT_PROMPT

    state.msg_num += 1
    state.session.add_system_message("[/reflect]", model=state.cur_model)

    try:
        coro = gsagent.run_agent_interactive(
            REFLECT_PROMPT, model=state.cur_model, working_dir=state.workdir,
            is_continuation=(state.msg_num > 1), session=state.session, mode=state.mode_state["mode"],
        )
        state.last_elapsed, _ = await _run_with_interrupt(coro, state.session)
    except Exception:
        logger.exception("/reflect failed")

    _print_response_separator()


async def _handle_switch_api(act: SlashResult, state: InteractiveState) -> None:
    logger.info("switch_api: → {!r} model={!r}", act.switch_api, act.switch_api_model)
    from apis.agent_adapter import create_api_session, restore_api_session_history
    if act.switch_api == "":
        return
    create_api_session(act.switch_api, act.switch_api_model or "")
    from apis.registry import get_definition
    _defn = get_definition(act.switch_api)
    if _defn and act.switch_api_model:
        _minfo = _defn.get_model_info(act.switch_api_model)
        state.cur_model = _minfo.display_name if _minfo else act.switch_api_model
    elif act.switch_api_model:
        state.cur_model = act.switch_api_model

    restore_api_session_history(state.session)
    state.pending_context = None
    state.msg_num = state.session.message_count
