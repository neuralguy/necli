import asyncio
import subprocess

from rich.console import Console
from rich.rule import Rule

from logger import logger
import config
from config.i18n import t as tr
import agent as gsagent
import session.storage as storage
from session import Session
from skills import reset_active_skills
from tools._paths import set_working_dir as _set_wd

from commands.helpers import (
    _run_with_interrupt,
    _print_session_switch,
    _print_response_separator,
)
from commands.slash import SlashResult
from commands.interactive_state import InteractiveState

console = Console()


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

    if act.do_decompress:
        await _handle_decompress(state)
        return True

    if act.do_commit:
        _handle_commit(act, state)
        return True

    if act.undo_n is not None:
        _handle_undo(act, state)
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
        _handle_toggle_tool_format(state)
        return True

    if act.do_reflect:
        await _handle_reflect(state)
        return True

    if act.switch_api is not None:
        await _handle_switch_api(act, state)

    return True


async def _handle_switch_session(act: SlashResult, state: InteractiveState) -> None:
    sid = act.switch_session
    logger.info("switch_session: → {}", sid[:16])
    new_session = storage.load(sid)
    if not new_session:
        logger.warning("switch_session: not found {}", sid)
        console.print(f"  [red]{tr('sh.session_not_found', name=sid)}[/red]")
        console.print(f"  [dim]{tr('sh.see_sessions')}[/dim]")
        return

    state.save_session()
    state.session = new_session

    if state.session.last_model:
        state.cur_model = state.session.last_model

    from apis.agent_adapter import restore_api_session_history
    loaded = restore_api_session_history(state.session)
    state.pending_context = None
    state.msg_num = state.session.message_count
    _print_session_switch(state.session)
    console.print(
        f"  [green]✓[/green] [dim]{tr('sh.history_loaded_msgs', n=loaded)}[/dim]"
    )


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
            _cd_parts.append(f"$ tree -L 3\n{_tree_r.stdout.strip()}")
    except Exception as e:
        logger.debug("cd tree snapshot failed: {}", e)

        _cd_context = (
            f"User changed working directory to: {new_dir}\n\n"
            + "\n\n".join(_cd_parts)
        )
        state.pending_context = [{"role": "system", "content": _cd_context}]
        console.print(f"  [dim]{tr('sh.dir_context_loaded')}[/dim]")


async def _handle_compress(state: InteractiveState) -> None:
    logger.info(
        "compress: session={} msg_count={}",
        state.session.id[:16], state.session.message_count,
    )
    history_text = state.session.build_compress_text()
    if not history_text.strip():
        console.print(f"  [dim]{tr('slash.nothing_to_compress')}[/dim]")
        return

    from prompts import COMPRESS_PROMPT
    compress_prompt = COMPRESS_PROMPT + history_text

    from apis.agent_adapter import (
        api_compress_history, get_api_session, api_new_chat,
    )
    try:
        with console.status(f"[bold cyan]{tr('sh.compressing')}[/bold cyan]", spinner="dots"):
            compressed = await api_compress_history(compress_prompt)

        compressed = compressed.strip()
        if not compressed:
            console.print(f"  [red]✗ {tr('sh.compress_empty')}[/red]")
            return

        state.session.compress_reset(compressed, model=state.cur_model)
        storage.save(state.session)

        from tools.file_ops.read import clear_read_cache
        clear_read_cache()

        await api_new_chat()
        api_sess = get_api_session()
        if api_sess is not None:
            api_sess.add_system(compressed)

        state.pending_context = None
        state.msg_num = 0

        console.print(f"  [green]✓[/green] {tr('sh.history_compressed')}")
        console.print()
    except Exception as e:
        console.print(f"\n  [red]✗ {tr('sh.compress_error', error=e)}[/red]")


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

    from prompts import COMPRESS_PROMPT
    compress_prompt = COMPRESS_PROMPT + history_text

    from apis.agent_adapter import (
        api_compress_history, api_new_chat, restore_api_session_history,
    )
    with console.status(f"[bold cyan]{tr('sh.compressing')}[/bold cyan]", spinner="dots"):
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


async def _handle_decompress(state: InteractiveState) -> None:
    """Restores original history before the last /compress."""
    sess = state.session
    pre = getattr(sess, "_pre_compress_messages", None)
    if not pre:
        console.print(f"  [dim]{tr('sh.no_backup')}[/dim]")
        return

    from session.message import Message

    logger.info(
        "decompress: session={} restoring {} messages",
        sess.id[:16], len(pre),
    )

    # Очищаем сжатые сообщения и восстанавливаем оригинал
    sess.messages = [Message.from_dict(m) for m in pre]
    sess._pre_compress_messages = None
    sess._pre_compress_at = None
    sess._compressed_stats = None
    sess._cost_cache = None

    storage.save(sess)

    # Пересоздаём API-сессию с восстановленной историей
    from apis.agent_adapter import api_new_chat, restore_api_session_history
    await api_new_chat()
    loaded = restore_api_session_history(sess)

    state.msg_num = sess.message_count
    state.pending_context = None

    console.print(
        f"  [green]✓[/green] {tr('sh.history_restored', n=sess.message_count, loaded=loaded)}"
    )


_BG_COMMIT_TASKS: set = set()


def _handle_commit(act: SlashResult, state: InteractiveState) -> None:
    """Запускает фоновый commit-агент. Не блокирует ввод — пользователь может
    параллельно давать новые задачи основному агенту."""
    api_id = config.get_active_api()
    model_id = config.get_active_api_model() or ""
    if not api_id:
        console.print(f"  [red]{tr('slash.api_not_configured')}[/red]")
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
            console.print(f"  [red]✗ {tr('sh.commit_failed', error=e)}[/red]")
            return
        logger.info("commit-agent done: %s", text.replace("\n", " ")[:200])

    task.add_done_callback(_done)
    console.print(f"  [cyan]⟳[/cyan] [dim]{tr('sh.commit_started')}[/dim]")


def _handle_undo(act: SlashResult, state: InteractiveState) -> None:
    """Откат файловых изменений из N последних раундов через undo-снапшоты."""
    from agent.undo_store import undo_rounds

    n = act.undo_n or 1
    logger.info("undo: requested {} round(s) in {}", n, state.workdir)
    try:
        ok, reverted, changed = undo_rounds(state.workdir, n)
    except Exception as e:
        logger.exception("undo failed")
        console.print(f"  [red]✗ {tr('sh.undo_error', error=e)}[/red]")
        return

    if not ok:
        console.print(f"  [dim]{tr('sh.no_undo_store')}[/dim]")
        return
    if reverted == 0 or not changed:
        console.print(f"  [dim]{tr('sh.undo_none')}[/dim]")
        return

    from tools.file_ops.read import clear_read_cache
    clear_read_cache()

    preview = ", ".join(changed[:5]) + ("…" if len(changed) > 5 else "")
    if reverted > 0:
        msg = tr('sh.undo_done', n=reverted, files=len(changed))
        arrow = "↶"
    else:
        msg = tr('sh.redo_done', n=-reverted, files=len(changed))
        arrow = "↷"
    console.print(f"  [yellow]{arrow}[/yellow] {msg} [dim]{preview}[/dim]")


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
    state.session = Session()
    state.msg_num = 0
    state.pending_context = None
    state.prompt_input.clear_images()
    try:
        from ui.terminal_title import set_session_terminal_title
        set_session_terminal_title(state.session)
    except Exception:
        logger.debug("new chat terminal title update failed", exc_info=True)
    console.print(f"  [yellow]↻[/yellow] {tr('sh.new_chat')} [dim]{state.session.id}[/dim]")


async def _handle_branch(state: InteractiveState) -> None:
    """Создаёт новую сессию-форк с копией текущей истории.

    Текущая сессия сохраняется как есть; работа продолжается в новой
    сессии, чья история — независимая копия сообщений текущей.
    """
    from session.message import Message

    old = state.session
    state.save_session()
    logger.info("branch: from {} ({} msgs)", old.id[:16], len(old.messages))

    new_session = Session()
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
    loaded = restore_api_session_history(new_session)

    console.print(
        f"  [yellow]⑃[/yellow] {tr('sh.branched', src=old.id[:16])}"
        f" [dim]{new_session.id} ({loaded} msgs)[/dim]"
    )


def _handle_toggle_think(state: InteractiveState) -> None:
    """Переключает think-флаг (ортогонален mode). Сохраняется между сессиями."""
    state.think_enabled = not state.think_enabled
    state.think_changed = True
    config.set_value("think_enabled", state.think_enabled)
    if state.think_enabled:
        console.print(f"  [magenta]💭[/magenta] [bold magenta]{tr('sh.think_on')}[/bold magenta]")
    else:
        console.print(f"  [dim]💭 {tr('sh.think_off')}[/dim]")


def _handle_toggle_tool_format(state: InteractiveState) -> None:
    """Переключает глобальный force-native флаг для tool calls.

    True  → все API-запросы принудительно используют native function calling.
    False → используется per-provider настройка (default).
    """
    current = bool(config.get("tool_format_force_native", False))
    new_val = not current
    config.set_value("tool_format_force_native", new_val)
    if new_val:
        console.print(f"  [cyan]⚙[/cyan] [bold cyan]{tr('sh.tool_format_native')}[/bold cyan]")
    else:
        console.print(f"  [dim]⚙ {tr('sh.tool_format_default')}[/dim]")



async def _handle_reflect(state: InteractiveState) -> None:
    console.print()
    console.print(Rule(characters="═", style="magenta"))
    console.print(
        f"  [magenta bold]{tr('sh.reflect')}[/magenta bold]"
        f" [dim]{tr('sh.reflect_hint')}[/dim]"
    )
    console.print()

    from prompts import REFLECT_PROMPT

    state.msg_num += 1
    state.session.add_system_message("[/reflect]", model=state.cur_model)

    try:
        coro = gsagent.run_agent_interactive(
            REFLECT_PROMPT, model=state.cur_model, working_dir=state.workdir,
            is_continuation=(state.msg_num > 1), session=state.session, mode=state.mode_state["mode"],
        )
        state.last_elapsed, _ = await _run_with_interrupt(coro, state.session)
    except Exception as e:
        logger.exception("/reflect failed")
        console.print(f"  [red]✗ /reflect: {e}[/red]")

    _print_response_separator()


async def _handle_switch_api(act: SlashResult, state: InteractiveState) -> None:
    logger.info("switch_api: → {!r} model={!r}", act.switch_api, act.switch_api_model)
    from apis.agent_adapter import create_api_session, restore_api_session_history
    if act.switch_api == "":
        console.print("  [yellow]Browser mode unavailable in API-only build.[/yellow]")
        return
    create_api_session(act.switch_api, act.switch_api_model or "")
    from apis.registry import get_definition
    _defn = get_definition(act.switch_api)
    if _defn and act.switch_api_model:
        _minfo = _defn.get_model_info(act.switch_api_model)
        state.cur_model = _minfo.display_name if _minfo else act.switch_api_model
    elif act.switch_api_model:
        state.cur_model = act.switch_api_model

    loaded = restore_api_session_history(state.session)
    state.pending_context = None
    state.msg_num = state.session.message_count
    if loaded:
        console.print(
            f"  [dim]{tr('sh.provider_switched', n=loaded)}[/dim]"
        )