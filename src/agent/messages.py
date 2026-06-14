"""Message building utilities for agent loop."""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from system_prompt import build_system_prompt, build_tool_results
from planner import Plan

_TREE_IGNORE = frozenset({
    "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".egg-info", ".tox", ".nox",
    ".cache", ".idea", ".vscode", ".git",
})
_TREE_MAX_DEPTH = 2
_TREE_MAX_ENTRIES = 500


def _build_tree_lines(root: Path, max_depth: int = _TREE_MAX_DEPTH) -> list[str]:
    """Минимальный нативный tree -L 2 с фильтрацией IGNORE_DIRS. Без size."""
    lines = [f"{root.name or str(root)}/"]
    count = [0]

    def _walk(dir_path: Path, prefix: str, depth: int):
        if count[0] >= _TREE_MAX_ENTRIES:
            return
        try:
            entries = sorted(
                (e for e in dir_path.iterdir() if e.name not in _TREE_IGNORE and not e.name.startswith(".")),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except (PermissionError, OSError):
            return
        for i, entry in enumerate(entries):
            if count[0] >= _TREE_MAX_ENTRIES:
                lines.append(f"{prefix}... (truncated)")
                return
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                count[0] += 1
                if depth < max_depth:
                    _walk(entry, prefix + extension, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")
                count[0] += 1

    _walk(root, "", 1)
    return lines

logger = logging.getLogger(__name__)

_TRUNCATION_CHAR_THRESHOLD = 45000
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
        root = Path(working_dir)
        date_str = datetime.now().strftime("%a %b %d %H:%M:%S %Z %Y").strip()
        tree_lines = await asyncio.to_thread(_build_tree_lines, root)
        out = [
            f"Working directory: {working_dir}",
            f"Today's date: {date_str}",
        ]
        if tree_lines:
            out.append("Project structure:\n" + "\n".join(tree_lines))
        return "\n".join(out)
    except Exception as e:
        logger.debug("Proof collection failed: %s", e)
        return f"(proof collection failed: {e})"


async def gather_dir_context(working_dir: str) -> str:
    """Контекст каталога для первого сообщения.

    AGENTS.md больше НЕ инжектится автоматически — он раздут (10k+ токенов) и
    нужен не каждую сессию. Файл виден в дереве; агент читает его через
    read_files, когда задача того требует. Возвращаем лишь короткое
    напоминание о его наличии, если файл есть.
    """
    agents_path = os.path.join(working_dir, "AGENTS.md")
    if os.path.isfile(agents_path):
        return (
            "Note: AGENTS.md exists in the working dir (project-specific rules, "
            "pitfalls, conventions). Read it via read_files when a task touches "
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
    if text.endswith(","):
        return True
    return False


async def build_first_message(
    user_text: str,
    working_dir: str,
    history: list[dict] | None = None,
    plan: "Plan | None" = None,
    include_system: bool = True,
) -> str:
    """Строит первое сообщение: [system prompt +] dir context + [plan] + [history] + user text.

    include_system=False используется в API-режиме, где системный промпт
    передаётся отдельно через system_prompt= параметр api_send_message.
    """
    if include_system:
        proof, dir_context = await asyncio.gather(
            gather_proof(working_dir),
            gather_dir_context(working_dir),
        )
        system = build_system_prompt(proof=proof)
        parts = [system]
    else:
        dir_context = await gather_dir_context(working_dir)
        parts = []
    if dir_context:
        parts.append("\n" + dir_context)
    if plan and plan.steps and not plan.is_complete:
        from prompts import ACTIVE_PLAN_NOTICE
        parts.append("\n" + ACTIVE_PLAN_NOTICE.format(plan=plan.render_for_context()))
    if history:
        from prompts import CONVERSATION_CONTEXT_HEADER, CONVERSATION_CONTEXT_FOOTER
        parts.append("\n" + CONVERSATION_CONTEXT_HEADER)
        for msg in history:
            role = msg["role"].upper()
            cnt = msg["content"]
            if len(cnt) > 2000:
                cnt = cnt[:1000] + "\n...(truncated)...\n" + cnt[-500:]
            parts.append(f"{role}:\n{cnt}")
        parts.append(CONVERSATION_CONTEXT_FOOTER)
    from skills import consume_pending_messages
    skill_msgs = consume_pending_messages()
    if skill_msgs:
        parts.append("\n" + "\n\n".join(skill_msgs))

    parts.append("\n" + (user_text or ""))
    return "\n".join(parts)


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
    """Добавки к результатам раунда: план + project_check + fs-изменения + статистика.

    Это НЕ часть вывода инструментов — в native режиме отправляется отдельным
    HumanMessage, чтобы не попасть внутрь ToolMessage и не путать модель.
    Возвращает "" если добавок нет.
    """
    parts: list[str] = []

    if plan and plan.steps:
        parts.append(plan.render_for_context())

    # Project-level checker (ruff/mypy/tsc) на изменённых файлах раунда
    if working_dir and step_tracker and step_tracker.files_changed:
        try:
            from tools.file_ops.project_check import run_project_check
            check_block = run_project_check(working_dir, set(step_tracker.files_changed))
            if check_block:
                parts.append(check_block)
        except Exception as e:
            logger.debug("project_check failed: %s", e)

    # Внешние изменения файлов (не от агента) с прошлого раунда
    if ctx is not None and working_dir:
        try:
            from agent.fs_watcher import take_snapshot_throttled, diff_snapshots, format_changes_block
            new_snap = take_snapshot_throttled(working_dir)
            old_snap = ctx.last_fs_snapshot
            if old_snap is not None:
                own = set(step_tracker.files_changed) if step_tracker else set()
                changes = diff_snapshots(old_snap, new_snap, own_paths=own)
                if changes:
                    block = format_changes_block(changes)
                    if block:
                        parts.append(block)
            ctx.last_fs_snapshot = new_snap
        except Exception as e:
            logger.debug("fs_watcher failed: %s", e)

    # Статистика проекта и шага
    if working_dir:
        from agent.project_stats import build_stats_line, StepTracker
        tracker = step_tracker or StepTracker()
        stats_line = build_stats_line(working_dir, tracker)
        if stats_line:
            parts.append(f"[{stats_line}]")

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
    from prompts import CONTINUE_MESSAGE
    return CONTINUE_MESSAGE






