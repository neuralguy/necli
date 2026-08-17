"""Message building utilities for agent loop."""

import logging
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING

from system_prompt import build_tool_results

if TYPE_CHECKING:
    from planner import Plan

logger = logging.getLogger(__name__)

_TRUNCATION_CHAR_THRESHOLD = 45000

# Максимальная длина сообщения истории до усечения (первые 1000 + ... + последние 500)
_HISTORY_TRUNCATE_LIMIT = 2000
_HISTORY_TRUNCATE_HEAD = 1000
_HISTORY_TRUNCATE_TAIL = 500


def truncate_history_content(content: str) -> str:
    """Урезает длинное сообщение истории: первые 1000 + ...(truncated)... + последние 500."""
    if len(content) > _HISTORY_TRUNCATE_LIMIT:
        return (
            content[:_HISTORY_TRUNCATE_HEAD]
            + "\n...(truncated)...\n"
            + content[-_HISTORY_TRUNCATE_TAIL:]
        )
    return content


def _truncate(text: str, max_len: int | None = None) -> str:
    if text is None:
        return ""
    if max_len is None:
        max_len = 50000
    # Guard: с max_len < 160 расчёт half (= max_len//2 - 80) даёт <=0 и обрезка
    # выдала бы пустые head/tail + мету. Просто отдаём текст как есть.
    if max_len < 160 or len(text) <= max_len:
        return text
    from agent.result_cache import store as _store_full

    rid = _store_full(text)
    half = max_len // 2 - 80
    head = text[:half]
    tail = text[-half:]
    shown = len(head) + len(tail)
    return (
        head
        + f"\n\n... [{shown} of {len(text)} chars shown, {len(text) - shown} skipped — "
        + f'expand via call expand_tool_result {{"id": "{rid}"}}] ...\n\n'
        + tail
    )


async def gather_proof(working_dir: str) -> str:
    try:
        # Время до секунд меняло system prompt на каждом ходе и сбрасывало
        # provider prompt cache. Для агента достаточно стабильной даты.
        date_str = datetime.now().strftime("%a %b %d %Y").strip()
        out = [
            f"Working directory: {working_dir}",
            f"Today's date: {date_str}",
        ]
        return "\n".join(out)
    except Exception as e:
        logger.debug("Proof collection failed: %s", e)
        return f"(proof collection failed: {e})"


async def gather_dir_context(working_dir: str) -> str:
    """Контекст каталога для первого сообщения.

    AGENTS.md больше НЕ инжектится автоматически — он раздут (10k+ токенов) и
    нужен не каждую сессию. Файл виден в дереве; агент читает его через
    read, когда задача того требует. Возвращаем лишь короткое
    напоминание о его наличии, если файл есть.
    """
    agents_path = os.path.join(working_dir, "AGENTS.md")
    if os.path.isfile(agents_path):
        return (
            "Note: AGENTS.md exists in the working dir (project-specific rules, "
            "pitfalls, conventions). Read it via read when a task touches "
            "areas it covers — do NOT assume its contents."
        )
    return ""


def is_api_proxy_error(text: str) -> bool:
    """Проверяет, является ли ответ ошибкой 502/503 или request aborted от API.

    Эвристика по тексту ответа: завязана на конкретный прокси (ask_proxy) и
    набор кодов (502/503/524). Узкая намеренно — ложноположительное «continue»
    дороже, чем пропуск редкого кода; расширять при появлении новых прокси.
    """
    if not text:
        return False
    t = text.strip().lower()
    if "request aborted" in t and "ask_proxy" in t:
        return True
    return "http error" in t and ("502" in t or "503" in t or "524" in t) and "ask_proxy" in t


def is_likely_truncated(text: str) -> bool:
    if len(text) < _TRUNCATION_CHAR_THRESHOLD:
        return False
    if text.count("```") % 2 != 0:
        return True
    return bool(text.endswith(","))


async def build_first_message(
    user_text: str,
    working_dir: str,
    history: list[dict] | None = None,
    plan: "Plan | None" = None,
    session_dir: str | None = None,
) -> str:
    """Строит первое сообщение: dir context + [plan] + [history] + user text.

    ВАЖНО: системный промпт сюда НЕ включается. Единственный источник правды для
    SystemMessage — apis.agent_adapter.api_send_message (через system_prompt=...).
    Раньше build_system_prompt подмешивался в начало user-сообщения, и на первом
    ходу промпт уходил ДВАЖДЫ: как role=system (из адаптера) и как role=user
    (отсюда). Теперь тело первого сообщения — только контекст директории, план,
    история и собственно текст пользователя.
    """
    from logger import log_span

    with log_span("context.build", message_type="first_message"):
        return await _build_first_message_impl(
            user_text,
            working_dir,
            history=history,
            plan=plan,
            session_dir=session_dir,
        )


async def _build_first_message_impl(
    user_text: str,
    working_dir: str,
    history: list[dict] | None = None,
    plan: "Plan | None" = None,
    session_dir: str | None = None,
) -> str:
    dir_context = await gather_dir_context(working_dir)
    parts: list[str] = []
    if dir_context:
        parts.append(dir_context)
    if plan and plan.steps and not plan.is_complete:
        from system_prompt import ACTIVE_PLAN_NOTICE

        parts.append("\n" + ACTIVE_PLAN_NOTICE.format(plan=plan.render_for_context()))
    if session_dir:
        try:
            from session.notes import format_session_notes_block

            notes = format_session_notes_block(session_dir)
            if notes:
                parts.append("\n" + notes)
        except Exception:
            logger.debug("session notes load failed", exc_info=True)
    if history:
        from system_prompt import CONVERSATION_CONTEXT_FOOTER, CONVERSATION_CONTEXT_HEADER

        parts.append("\n" + CONVERSATION_CONTEXT_HEADER)
        for msg in history:
            role = msg["role"].upper()
            cnt = truncate_history_content(msg["content"])
            parts.append(f"{role}:\n{cnt}")
        parts.append(CONVERSATION_CONTEXT_FOOTER)
    from skills import consume_pending_messages

    skill_msgs = consume_pending_messages()
    if skill_msgs:
        parts.append("\n" + "\n\n".join(skill_msgs))

    parts.append("\n" + (user_text or ""))
    # lstrip: первый блок (dir_context) может отсутствовать, тогда остальные
    # секции начинаются с "\n" — убираем ведущий перевод строки, чтобы тело
    # не открывалось пустой строкой.
    return "\n".join(parts).lstrip("\n")


def _result_dicts(results) -> list[dict]:
    """ToolResult → list[dict] с применённой truncation. Общий хелпер для
    текстового payload и структурной (native) доставки результатов."""
    result_dicts = []
    for r in results:
        d = r.to_dict()
        if d.get("output") is None:
            d["output"] = ""
        if not d.get("full_content"):
            d["output"] = _truncate(d["output"])
        d.pop("full_content", None)
        result_dicts.append(d)
    return result_dicts


def build_structured_tool_results(results) -> list[dict]:
    """Структурные результаты для native function-calling доставки.

    Каждый элемент: {name, command, exit_code, output} с той же truncation,
    что и текстовый payload. Адаптер сопоставит их pending tool_call'ам
    по имени (name + FIFO), формируя по одному ToolMessage на каждый id —
    БЕЗ склейки в один blob (поэтому '---' внутри output безопасен).
    """
    return _result_dicts(results)


def _build_tool_results_payload(results) -> str:
    """Плоский '$ cmd\\n<output>' blob (text/fenced режим). Без extras."""
    return build_tool_results(_result_dicts(results))


def _build_result_extras(plan=None, working_dir=None, step_tracker=None, ctx=None) -> str:
    """Добавки к результатам раунда: план + проверка TypeScript + fs-изменения.

    Это НЕ часть вывода инструментов — в native режиме отправляется отдельным
    HumanMessage, чтобы не попасть внутрь ToolMessage и не путать модель.
    Возвращает "" если добавок нет.
    """
    parts: list[str] = []
    _extras_started = time.monotonic()
    _timings: dict[str, float] = {}

    if plan and plan.steps:
        parts.append(plan.render_for_context())

    # Проверка TypeScript на изменённых файлах раунда.
    if working_dir and step_tracker and step_tracker.files_changed:
        try:
            _t = time.monotonic()
            from tools.file_ops.project_check import run_project_check

            check_block = run_project_check(working_dir, set(step_tracker.files_changed))
            _timings["project_check"] = time.monotonic() - _t
            if check_block:
                parts.append(check_block)
        except Exception as e:
            logger.debug("project_check failed: %s", e)

    # Внешние изменения файлов (не от агента) с прошлого раунда
    if ctx is not None and working_dir:
        try:
            _t = time.monotonic()
            from agent.fs_watcher import (
                diff_snapshots,
                format_changes_block,
                refresh_snapshot,
                take_snapshot_throttled,
            )

            old_snap = ctx.last_fs_snapshot
            if step_tracker and step_tracker.files_changed:
                new_snap = refresh_snapshot(working_dir)
            else:
                new_snap = take_snapshot_throttled(working_dir)
            if old_snap is not None:
                own = set()
                if step_tracker:
                    for path in step_tracker.files_changed:
                        if not os.path.isabs(path):
                            path = os.path.join(working_dir, path)
                        try:
                            path = os.path.relpath(os.path.abspath(path), working_dir)
                        except ValueError:
                            path = os.path.abspath(path)
                        own.add(path)
                changes = diff_snapshots(old_snap, new_snap, own_paths=own)
                if changes:
                    block = format_changes_block(changes)
                    if block:
                        parts.append(block)
            ctx.last_fs_snapshot = new_snap
            _timings["fs_snapshot"] = time.monotonic() - _t
        except Exception as e:
            logger.debug("fs_watcher failed: %s", e)

    _total = time.monotonic() - _extras_started
    if _total >= 0.25:
        logger.warning(
            "slow result extras: total=%.3fs project_check=%.3fs fs=%.3fs",
            _total,
            _timings.get("project_check", 0.0),
            _timings.get("fs_snapshot", 0.0),
        )
    return "\n\n".join(parts)


def _build_result_message(results, plan=None, working_dir=None, step_tracker=None, ctx=None):
    """Плоский payload результатов + extras одним текстом (text/fenced режим,
    а также run_agent без сессии). Native режим вместо этого использует
    build_structured_tool_results + _build_result_extras раздельно."""
    payload = _build_tool_results_payload(results)
    extras = _build_result_extras(plan, working_dir, step_tracker, ctx)
    if extras:
        return payload + "\n\n" + extras
    return payload


def build_continue_message() -> str:
    from system_prompt import CONTINUE_MESSAGE

    return CONTINUE_MESSAGE
