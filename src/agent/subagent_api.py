"""API-режим для субагентов.

Каждый субагент работает со своим ApiSession (изолированный контекст),
своим git worktree (изолированная ФС) и поддерживает выбор модели и/или
провайдера, нативные tool calls и текстовый fallback. Стримит в общий
SubagentBuffer для мультиплексного отображения.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import tools
from tools import parse_tool_calls, strip_tool_calls
from tools._paths import use_working_dir, get_working_dir
from tools.registry import execute_call
from system_prompt import build_tool_results, build_system_prompt
from agent.sanitizer import sanitize_response
from agent.subagent_render import SubagentBuffer
from agent.subagent_git import (
    WorktreeHandle, commit_worktree, summarize_changes,
    summarize_worktree_changes, cleanup_worktree,
)

from apis.agent_adapter import (
    ApiSession,
    _tool_calls_to_text_blocks,
    _ensure_tool_call_ids,
    _content_to_text,
)
from apis.registry import get_provider, resolve_api_model
from apis._retry import with_throttle_retry, stream_with_throttle_retry
from apis.tool_schemas import get_tool_schemas

from apis.messages import (
    HumanMessage, SystemMessage, AIMessage, ToolMessage,
)


logger = logging.getLogger(__name__)

MAX_SUBAGENT_ITERATIONS = 120

_ROLE_PROFILES: dict[str, str] = {
    "coder": (
        "Your ROLE is CODER. Implement code changes precisely. Write/patch files, "
        "run shell to test. Keep edits focused, do not refactor unrelated code."
    ),
    "researcher": (
        "Your ROLE is RESEARCHER. Gather facts from the codebase and the web. "
        "Deliver findings with concrete references (file:line, URLs)."
    ),
    "reviewer": (
        "Your ROLE is REVIEWER. Audit code for bugs, style, security. Use "
        "lsp_diagnostics on changed files. Deliver a concrete review with "
        "file:line references and severity."
    ),
    "planner": (
        "Your ROLE is PLANNER. Decompose the task into a concrete step-by-step "
        "plan backed by real file reads. Deliver an ordered actionable plan "
        "with exact paths."
    ),
    "coordinator": (
        "Your ROLE is COORDINATOR. You run FIRST, before the implementer subagents. "
        "Read the relevant code, then DECIDE the shared contracts the other subagents "
        "must all agree on: exact module/function/class names, signatures, file paths, "
        "data shapes, API endpoints. Write these decisions into the SHARED SCRATCHPAD "
        "(append, never overwrite) as a clear, unambiguous spec — this is your ONLY "
        "deliverable that matters. The implementer subagents depend_on you and will be "
        "given your output. Be decisive: pick concrete names, do not leave choices open."
    ),
}


def _normalize_role(role: Optional[str]) -> Optional[str]:
    if not role:
        return None
    r = role.strip().lower()
    return r if r in _ROLE_PROFILES else None


def _shared_scratchpad_path(working_dir: str) -> str:
    """Путь к общему scratchpad. working_dir субагента — это worktree:
    .data/subagents/<run-id>/sub-<N>/. Scratchpad лежит на уровень выше,
    общий для всех субагентов прогона: .data/subagents/<run-id>/shared.md."""
    import os
    return os.path.join(os.path.dirname(working_dir.rstrip("/")), "shared.md")


def _read_scratchpad(working_dir: str) -> str:
    import os
    path = _shared_scratchpad_path(working_dir)
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="ignore") as fh:
                return fh.read(8000).strip()
    except Exception:
        logger.debug("subagent read scratchpad failed", exc_info=True)
    return ""


def _project_context(working_dir: str) -> str:
    """Краткий контекст проекта для субагента: статистика + начало AGENTS.md."""
    import os
    parts = []
    try:
        from agent.project_stats import count_project_stats, format_project_stats
        fc, tl = count_project_stats(working_dir)
        if fc:
            parts.append(format_project_stats(fc, tl))
    except Exception:
        logger.debug("subagent project_stats failed", exc_info=True)
    for fname in ("AGENTS.md", "README.md"):
        path = os.path.join(working_dir, fname)
        try:
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(800).strip()
                if head:
                    parts.append(f"--- {fname} (head) ---\n{head}")
                break
        except Exception:
            logger.debug("subagent read %s failed", fname, exc_info=True)
    return "\n".join(parts)

# Инструменты, недоступные субагентам (poll — нет интерактива, subagent — нет
# вложенности). web_search разрешён: субагент должен уметь искать в сети.
_BLOCKED_FOR_SUBAGENTS = frozenset({"poll", "subagent", "workflow"})


def resolve_subagent_model(
    requested: Optional[str],
    default_provider_id: str,
    default_model_id: str,
) -> tuple[str, str]:
    """Разрешает запрошенную модель в (provider_id, model_id).

    Стратегия:
      1. Пусто → дефолт главного агента.
      2. Сначала ищем модель ВНУТРИ провайдера главного агента — это спасает
         от ситуации, когда `claude-sonnet-4-6` есть и в anthropic, и в onlysq,
         а главный сидит на onlysq. Глобальный resolve_api_model взял бы
         первое совпадение по порядку загрузки definitions (часто anthropic
         идёт раньше) — и субагент уехал бы в Anthropic API с onlysq-ключом.
      3. Если в провайдере главного нет — глобальный resolve_api_model.
      4. Иначе fallback на провайдер главного с моделью как есть.
    """
    if not requested or not requested.strip():
        return default_provider_id, default_model_id

    q = requested.strip()
    q_lower = q.lower()

    # 1. Точное совпадение в провайдере главного агента.
    from apis.registry import get_definitions
    home = get_definitions().get(default_provider_id)
    if home and home.enabled:
        for m in home.models:
            if m.id.lower() == q_lower or m.display_name.lower() == q_lower:
                return (default_provider_id, m.id)
        # Подстрока тоже допустима, но только если единственная.
        sub_matches = [
            m.id for m in home.models
            if q_lower in m.id.lower() or q_lower in m.display_name.lower()
        ]
        if len(sub_matches) == 1:
            return (default_provider_id, sub_matches[0])

    # 2. Глобальный поиск.
    found = resolve_api_model(q)
    if found:
        return found

    # 3. Fallback: провайдер главного, model_id как есть.
    logger.warning(
        f"Subagent model '{q}' not resolved, falling back to "
        f"provider={default_provider_id} model_id='{q}'"
    )
    return default_provider_id, q


class _ApiSubagentRunner:
    """Один субагент в API-режиме."""

    def __init__(
        self,
        index: int,
        prompt: str,
        mode: str,
        provider_id: str,
        model_id: str,
        proof: str,
        buffer: Optional[SubagentBuffer],
        status_cb,
        handle: WorktreeHandle,
        role: Optional[str] = None,
        dep_context: str = "",
        preset=None,
        project_root: str = "",
        isolate: bool = True,
    ):
        self.index = index
        self.prompt = prompt
        self.mode = mode
        self.provider_id = provider_id
        self.model_id = model_id
        self.proof = proof
        self.buffer = buffer
        self.status_cb = status_cb
        self.handle = handle
        self.isolate = isolate
        # isolate=True → работаем в изолированном worktree (handle.path);
        # isolate=False → пишем прямо в общую рабочую директорию проекта.
        self.working_dir = handle.path if handle is not None else (project_root or get_working_dir())
        self.role = _normalize_role(role)
        self.preset = preset  # AgentPreset | None
        self.dep_context = dep_context or ""

        self.session = ApiSession(provider_id, model_id)
        self.use_native = self.session.use_native_tools

    def _workspace_prompt(self) -> str:
        """Описание рабочего пространства субагента — изолированного или общего."""
        if self.handle is not None:
            return (
                "Your starting (and default) working directory is an isolated git worktree at:\n"
                f"  {self.working_dir}  (branch: {self.handle.branch})\n"
                "You may operate anywhere on the filesystem when the task needs it — `cd` is allowed "
                "and tools accept absolute paths. Edits you make to files INSIDE this worktree are "
                "committed to your branch by the orchestrator after you finish (changes outside it "
                "are NOT committed — use those only when the task explicitly requires it). Do NOT run "
                "any git command yourself (no commit/push/checkout/merge/rebase/reset). Keep changes "
                "focused on the task, don't touch unrelated files.\n\n"
            )
        return (
            "Your working directory is the SHARED project directory:\n"
            f"  {self.working_dir}\n"
            "There is NO isolation — your file edits land directly in the project, alongside "
            "other subagents running in parallel. You were given an INDEPENDENT slice of the work: "
            "stay strictly within it, touch ONLY the files your task names, and do NOT edit paths "
            "another subagent might be writing — concurrent writes to the same file will clobber "
            "each other. `cd` is allowed and tools accept absolute paths. Do NOT run any git "
            "command yourself (no commit/push/checkout/merge/rebase/reset).\n\n"
        )

    def _build_system_prompt(self) -> str:
        # Подменяем working directory в proof — субагент должен видеть свой worktree.
        proof = self.proof
        if "Working directory:" in proof:
            lines = proof.splitlines()
            for i, ln in enumerate(lines):
                if ln.startswith("Working directory:"):
                    lines[i] = f"Working directory: {self.working_dir}"
                    break
            proof = "\n".join(lines)

        # Единый источник правды: build_system_prompt. Субагенту явно задаём
        # native_tools=self.use_native и think_enabled=False — у субагента
        # свой фокусный протокол, THINK
        # ему не навязываем. mode='agent' тут номинален: ниже добавляется
        # subagent-specific mode_block с детальными правилами worktree/роли.
        base = build_system_prompt(
            proof=proof,
            mode="agent",
            working_dir=self.working_dir,
            think_enabled=False,
            native_tools=self.use_native,
        )
        mode_block = (
            "\n\n━━━ SUBAGENT MODE: AGENT ━━━\n"
            "You are an autonomous subagent. Execute the task to FULL completion and "
            "return a concrete result. All tools are available EXCEPT poll and subagent "
            "(web_search IS available — use it for any real-time/online info). "
            "NEVER ask the user questions — decide and act.\n"
            f"{self._workspace_prompt()}"
            "FINAL ANSWER FORMAT (reply with text only, no tool call, when done):\n"
            "  1. What you did — 1-3 bullet lines.\n"
            "  2. Files changed — path + one line each (or 'none').\n"
            "  3. How to verify — exact command(s) the main agent can run, or 'n/a'.\n"
            "Be terse and factual. The main agent reads ONLY your final text + the git diff.\n"
        )
        if self.role:
            role_desc = _ROLE_PROFILES[self.role]
            mode_block += f"\n━━━ ROLE ━━━\n{role_desc}\n"

        if self.preset:
            mode_block += (
                f"\n━━━ AGENT PRESET: {self.preset.name} ━━━\n"
                f"{self.preset.body}\n"
            )

        proj_ctx = _project_context(self.working_dir)
        if proj_ctx:
            mode_block += f"\n━━━ PROJECT CONTEXT ━━━\n{proj_ctx}\n"

        if self.dep_context:
            mode_block += (
                "\n━━━ RESULTS FROM DEPENDENCY SUBAGENTS ━━━\n"
                "These ran before you; build on their output:\n"
                f"{self.dep_context}\n"
            )

        import os as _os
        progress_path = _os.path.join(
            _os.path.dirname(self.working_dir.rstrip("/")), "progress.md",
        )
        mode_block += (
            "\n━━━ PEER PROGRESS LOG ━━━\n"
            "An incremental log of THIS run's subagents is written at:\n"
            f"  {progress_path}\n"
            "Each peer is appended there the moment it FINISHES (DONE/ERROR) — "
            "read it to inspect already-completed peers without waiting for the "
            "whole run. It lives OUTSIDE your worktree and is NOT committed.\n"
        )

        scratch = _read_scratchpad(self.working_dir)
        mode_block += (
            "\n━━━ SHARED SCRATCHPAD ━━━\n"
            "A shared notes file is available to ALL subagents of this run at:\n"
            f"  {_shared_scratchpad_path(self.working_dir)}\n"
            "Read it for contracts/interfaces other subagents agreed on. "
            "Append (never overwrite) your own decisions other subagents may "
            "need — use shell `cat >>` or read_files+write_file carefully. "
            "It lives OUTSIDE your worktree and is NOT committed.\n"
        )
        if scratch:
            mode_block += f"Current scratchpad content:\n{scratch}\n"

        # text-mode формат теперь внутри build_system_prompt (native_tools).
        return base + mode_block

    def _tools_schema(self) -> list[dict]:
        schemas = get_tool_schemas("agent")
        return [
            s for s in schemas
            if s.get("function", {}).get("name") not in _BLOCKED_FOR_SUBAGENTS
        ]

    def _bind_llm(self, use_tools: bool) -> tuple:
        """Возвращает (llm, bound_ok). bound_ok=False если bind_tools упал —
        вызывающий код должен откатиться на fenced-режим без tools."""
        llm = get_provider(self.provider_id, self.model_id)
        bound_ok = False
        if use_tools:
            try:
                if hasattr(llm, "streaming"):
                    llm.streaming = False
            except Exception:
                pass
            try:
                tools_schema = self._tools_schema()
                if tools_schema:
                    llm = llm.bind_tools(tools_schema, tool_choice="auto")
                    bound_ok = True
            except Exception as e:
                logger.warning(
                    f"Subagent {self.index} bind_tools failed, falling back to fenced: {e}"
                )
        else:
            try:
                if hasattr(llm, "streaming"):
                    llm.streaming = True
            except Exception:
                pass
        return llm, bound_ok

    async def _call_model(self) -> tuple[str, list[dict]]:
        """Делает один вызов модели, возвращает (raw_text, tool_calls)."""
        # tools биндим если у нас native function calling и есть хоть один
        # разрешённый tool.
        want_tools = self.use_native and bool(self._tools_schema())
        llm, bound_ok = self._bind_llm(want_tools)
        # Если хотели native но bind_tools не сработал — провайдер не умеет,
        # переключаемся на стрим без tools (модель будет использовать fenced).
        use_tools = want_tools and bound_ok

        on_chunk = self.buffer.on_chunk if self.buffer else None

        raw_text = ""
        tool_calls: list[dict] = []

        if use_tools:
            result = await with_throttle_retry(lambda: llm.ainvoke(self.session.messages))
            raw_text = _content_to_text(getattr(result, "content", result))
            tool_calls = list(getattr(result, "tool_calls", []) or [])
            tool_calls = _ensure_tool_call_ids(tool_calls)
            if self.buffer:
                self.buffer.on_usage(getattr(result, "usage_metadata", None))
            if on_chunk is not None:
                if raw_text:
                    on_chunk(raw_text)
                if tool_calls:
                    on_chunk(raw_text + _tool_calls_to_text_blocks(tool_calls))
        elif on_chunk is not None:
            final_chunk = await stream_with_throttle_retry(
                lambda: llm.astream(self.session.messages),
                on_chunk,
                on_tool_chunk=lambda c: None,
            )
            raw_text = _content_to_text(getattr(final_chunk, "content", ""))
            tool_calls = list(getattr(final_chunk, "tool_calls", []) or [])
            if self.buffer:
                self.buffer.on_usage(getattr(final_chunk, "usage_metadata", None))
            if tool_calls:
                tool_calls = _ensure_tool_call_ids(tool_calls)
                on_chunk(raw_text + _tool_calls_to_text_blocks(tool_calls))
        else:
            result = await with_throttle_retry(lambda: llm.ainvoke(self.session.messages))
            raw_text = _content_to_text(getattr(result, "content", result))
            tool_calls = list(getattr(result, "tool_calls", []) or [])
            tool_calls = _ensure_tool_call_ids(tool_calls)
            if self.buffer:
                self.buffer.on_usage(getattr(result, "usage_metadata", None))

        return raw_text, tool_calls

    def _append_assistant(self, raw_text: str, tool_calls: list[dict]) -> None:
        kwargs = {"content": raw_text}
        if tool_calls and self.use_native:
            kwargs["tool_calls"] = tool_calls
        self.session.messages.append(AIMessage(**kwargs))

    def _execute_tool_calls(
        self,
        text_calls: list[tools.ToolCall],
    ) -> list[tools.ToolResult]:
        """Синхронно выполняет список tool calls с фильтрацией блокированных.

        Выполняется внутри use_working_dir(self.working_dir): file-tool'ы
        и shell корректно резолвят пути в worktree этого субагента.
        """
        results = []
        with use_working_dir(self.working_dir):
            for call in text_calls:
                if call.tool_name in _BLOCKED_FOR_SUBAGENTS:
                    results.append(tools.ToolResult(
                        name=call.tool_name,
                        status="error",
                        output=f"Tool '{call.tool_name}' is not available for subagents.",
                        exit_code=1,
                        command=call.command,
                    ))
                    continue
                if self.buffer:
                    self.buffer.on_tool_start(call.tool_name, call.command, call.args)
                t0 = time.monotonic()
                try:
                    r = execute_call(call)
                except Exception as e:
                    logger.error(
                        f"Subagent {self.index} tool {call.tool_name} crashed: {e}",
                        exc_info=True,
                    )
                    r = tools.ToolResult(
                        name=call.tool_name,
                        status="error",
                        output=f"Tool crashed: {type(e).__name__}: {e}",
                        exit_code=1,
                        command=call.command,
                    )
                elapsed = time.monotonic() - t0
                results.append(r)
                if self.buffer:
                    preview = r.output[:200] if r.output else ""
                    self.buffer.on_tool_done(
                        output_preview=preview,
                        elapsed=elapsed,
                        error=(r.status == "error"),
                    )
        return results

    @staticmethod
    def _truncate_tool_output(text: str, limit: int = 30000) -> str:
        if len(text) <= limit:
            return text
        half = limit // 2
        return (
            text[:half]
            + f"\n... [{len(text)} chars, truncated] ...\n"
            + text[-half:]
        )

    def _append_tool_results_native(
        self,
        tool_calls: list[dict],
        results: list[tools.ToolResult],
    ) -> None:
        """Каждому tool_call_id — отдельный ToolMessage (name+FIFO).

        Та же стратегия, что в apis.agent_adapter.build_native_tool_messages
        для основного цикла. Здесь работаем с ToolResult напрямую и своей
        truncation; формат содержимого ('[error exit=N]' префикс) отличается —
        поэтому отдельная реализация, а не общий хелпер.
        """
        results_by_name = {}
        for r in results:
            results_by_name.setdefault(r.name, []).append(r)

        for tc in tool_calls:
            name = tc.get("name") or "shell"
            tc_id = tc.get("id") or ""
            bucket = results_by_name.get(name) or []
            r = bucket.pop(0) if bucket else None
            if r is None:
                content = f"No result for tool {name}."
            else:
                content = self._truncate_tool_output(r.output or "")
                if r.status == "error":
                    content = f"[error exit={r.exit_code}]\n{content}"
            self.session.messages.append(ToolMessage(
                content=content,
                tool_call_id=tc_id,
                name=name,
            ))

    def _append_tool_results_text(self, results: list[tools.ToolResult]) -> None:
        """Все результаты одним HumanMessage (для text-режима)."""
        result_dicts = []
        for r in results:
            d = r.to_dict()
            d["output"] = self._truncate_tool_output(d.get("output") or "")
            result_dicts.append(d)
        self.session.messages.append(HumanMessage(
            content=build_tool_results(result_dicts),
        ))

    async def run(self) -> tuple[str, int, Optional[str]]:
        """Запускает мини-агентный цикл. Возвращает (final_text, iterations, error).

        НЕ оборачиваем весь цикл в use_working_dir: между итерациями есть
        `await self._call_model()`, который возвращает управление в event loop,
        и параллельный субагент перезаписал бы тот же ContextVar — working_dir
        стал бы недетерминированным. Рабочий каталог устанавливается точечно в
        _execute_tool_calls (синхронный блок без await), где он и нужен
        handler'ам инструментов.
        """
        iterations = 0
        try:
            self.session.messages.append(SystemMessage(content=self._build_system_prompt()))
            self.session.messages.append(HumanMessage(content=self.prompt))

            raw_text = ""
            progress_nudges = 0
            for iterations in range(MAX_SUBAGENT_ITERATIONS):
                self.status_cb(self.index, f"Iteration {iterations + 1}")
                if self.buffer:
                    self.buffer.streaming_text = ""
                    self.buffer.on_iteration(iterations)

                raw_text, native_tool_calls = await self._call_model()
                raw_text = sanitize_response(raw_text)
                self._append_assistant(raw_text, native_tool_calls)

                # Собираем все tool calls: native + текстовые в raw_text
                text_calls = parse_tool_calls(raw_text)

                # Native tool_calls конвертируем в ToolCall для исполнения
                native_as_calls = []
                if native_tool_calls:
                    from apis.agent_adapter import _tool_calls_to_text_blocks as _blocks
                    blocks_text = _blocks(native_tool_calls)
                    native_as_calls = parse_tool_calls(blocks_text)

                # Если native_tool_calls есть — используем именно их (исходник истины)
                if native_tool_calls:
                    all_calls = native_as_calls
                else:
                    all_calls = text_calls

                if not all_calls:
                    final = strip_tool_calls(raw_text).strip()
                    if _looks_like_progress_only(final) and progress_nudges < 2:
                        progress_nudges += 1
                        self.session.messages.append(HumanMessage(
                            content=(
                                "Your last message looks like progress/status, not the required final answer. "
                                "Continue now. If you need tools, call them; otherwise provide the concrete "
                                "FINAL ANSWER FORMAT from the system instructions."
                            ),
                        ))
                        continue
                    if self.buffer:
                        self.buffer.on_done(final)
                    return final, iterations + 1, None

                results = self._execute_tool_calls(all_calls)

                if native_tool_calls:
                    self._append_tool_results_native(native_tool_calls, results)
                else:
                    self._append_tool_results_text(results)

            # Лимит итераций
            final = strip_tool_calls(raw_text).strip() + "\n\n[Subagent iteration limit]"
            if self.buffer:
                self.buffer.on_done(final)
            return final, iterations + 1, None

        except Exception as e:
            logger.error(f"Subagent {self.index} API run failed: {e}", exc_info=True)
            err = f"{type(e).__name__}: {e}"
            if self.buffer:
                self.buffer.on_error(err)
            # Возвращаем фактическое число выполненных итераций, а не 0.
            return "", iterations + 1, err


def _looks_like_progress_only(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    low = stripped.lower()
    markers = (
        "читаю", "прочитаю", "проверю", "соберу", "завершаю",
        "инструментальные вызовы", "не удалось продолжить",
        "i will", "i'll", "reading", "checking", "let me",
    )
    return len(stripped) < 300 and any(m in low for m in markers)


def _commit_message_for(task_prompt: str) -> str:
    """Делает короткий commit-msg из первой строки prompt'а."""
    first = (task_prompt or "").strip().splitlines()[0] if task_prompt else ""
    if len(first) > 80:
        first = first[:77] + "..."
    return f"subagent: {first}" if first else "subagent: task"


def _resolve_dependencies(tasks: list) -> tuple[list[list[int]], str | None]:
    """Топосортировка задач по depends_on (1-based) → волны индексов (0-based).

    Невалидные ссылки и циклы возвращаются как ошибка orchestration, а не
    запускаются "как получится": depends_on — контракт порядка выполнения.
    """
    n = len(tasks)
    deps: list[set[int]] = []
    errors: list[str] = []
    for i, task in enumerate(tasks):
        raw = getattr(task, "depends_on", None) or []
        ok = set()
        for d in raw:
            try:
                dep_num = int(d)
            except (TypeError, ValueError):
                errors.append(f"task {i + 1}: depends_on contains non-integer {d!r}")
                continue
            idx = dep_num - 1
            if idx == i:
                errors.append(f"task {i + 1}: depends_on references itself")
            elif not 0 <= idx < n:
                errors.append(f"task {i + 1}: depends_on references missing task {dep_num}")
            else:
                ok.add(idx)
        deps.append(ok)
    if errors:
        return [], "; ".join(errors)

    done: set[int] = set()
    waves: list[list[int]] = []
    remaining = set(range(n))
    while remaining:
        ready = [i for i in sorted(remaining) if deps[i] <= done]
        if not ready:
            cycle_nodes = ", ".join(str(i + 1) for i in sorted(remaining))
            return [], f"dependency cycle among task(s): {cycle_nodes}"
        waves.append(ready)
        done |= set(ready)
        remaining -= set(ready)
    return waves, None


def _truncate_dep_body(body: str, max_chars: int) -> str:
    if len(body) <= max_chars:
        return body
    half = max(1, max_chars // 2)
    omitted = len(body) - half * 2
    return (
        body[:half]
        + f"\n...[DEPENDENCY OUTPUT TRUNCATED: {omitted} chars omitted. "
        + "Use the peer progress log/shared scratchpad if exact details are required]...\n"
        + body[-half:]
    )


def _build_dep_context(task, results_by_index: dict, max_chars: int = 12000) -> str:
    """Собирает текст результатов задач-зависимостей для инъекции в промпт."""
    raw = getattr(task, "depends_on", None) or []
    parts = []
    for d in raw:
        try:
            idx = int(d) - 1
        except (TypeError, ValueError):
            continue
        res = results_by_index.get(idx)
        if res is None:
            continue
        body = (res.response or res.error or "").strip()
        if not body:
            continue
        body = _truncate_dep_body(body, max_chars)
        parts.append(f"--- Subagent {idx + 1} result ---\n{body}")
    return "\n\n".join(parts)


def _progress_log_path(run_dir: Optional[str]) -> Optional[str]:
    import os
    if not run_dir:
        return None
    return os.path.join(run_dir, "progress.md")


def _init_progress_log(run_dir: Optional[str], total: int) -> None:
    """Создаёт progress.md — инкрементальный лог завершившихся субагентов.

    Главный агент может читать его, не дожидаясь конца всех волн: каждая
    завершившаяся задача дописывается сюда сразу после финализации
    (commit + summarize), под заголовком `## Subagent N — DONE/ERROR`.
    """
    path = _progress_log_path(run_dir)
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                f"# Subagent progress ({total} tasks)\n\n"
                "Each subagent is appended here the moment it finishes — "
                "read this to study completed subagents WITHOUT waiting for "
                "all of them. The final tool result repeats everything.\n\n"
            )
    except Exception:
        logger.debug("subagent: init progress log failed", exc_info=True)


_PROGRESS_LOCK = asyncio.Lock()


async def _append_progress(run_dir: Optional[str], result, total: int) -> None:
    """Дописывает запись о завершившемся субагенте в progress.md."""
    path = _progress_log_path(run_dir)
    if not path:
        return
    n = result.task_index + 1
    if result.error:
        phase = f" [{result.phase}]" if getattr(result, "phase", "") else ""
        label = f" {result.label}" if getattr(result, "label", "") else ""
        block = [f"## Subagent {n}/{total}{phase}{label} [{result.mode}] — ERROR", result.error]
        if getattr(result, "branch", ""):
            block.append(f"branch: {result.branch}")
            if getattr(result, "has_changes", False):
                block.append("uncommitted changes kept for inspection")
                if getattr(result, "worktree_path", ""):
                    block.append(f"worktree: {result.worktree_path}")
                if getattr(result, "files_changed", None):
                    block.append(f"files ({len(result.files_changed)}):")
                    for f in result.files_changed[:30]:
                        block.append(f"  {f}")
                if getattr(result, "diff_stat", ""):
                    block.append("")
                    block.append(result.diff_stat)
            else:
                block.append("no changes")
        block.append("")
    else:
        phase = f" [{result.phase}]" if getattr(result, "phase", "") else ""
        label = f" {result.label}" if getattr(result, "label", "") else ""
        head = (
            f"## Subagent {n}/{total}{phase}{label} [{result.mode}] — DONE "
            f"({result.iterations} iters, {result.elapsed:.1f}s)"
        )
        block = [head, (result.response or "").strip()]
        if result.branch:
            block.append(f"branch: {result.branch}")
            if result.has_changes and result.commit_sha:
                block.append(
                    f"to inspect: git show {result.commit_sha[:12]}    "
                    f"to merge: git merge --no-ff {result.branch}"
                )
            elif not result.has_changes:
                block.append("no changes")
        block.append("")
    async with _PROGRESS_LOCK:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(block) + "\n")
        except Exception:
            logger.debug("subagent: append progress log failed", exc_info=True)


def _copy_git_metadata(result, handle) -> None:
    result.branch = handle.branch
    result.worktree_path = handle.path
    result.commit_sha = handle.commit_sha
    result.commits_count = handle.commits_count
    result.files_changed = list(handle.files_changed)
    result.diff_stat = handle.diff_stat
    result.has_changes = handle.has_changes


def _finalize_subagent(result, task, handle, project_root: str) -> None:
    """Finalize worktree. Failed subagents are never committed silently."""
    if handle is None:
        # isolate=False: правки уже в общей рабочей директории, git-метаданных нет.
        return
    try:
        if result.error:
            summarize_worktree_changes(handle)
            _copy_git_metadata(result, handle)
            if handle.has_changes:
                logger.warning(
                    "subagent_git: keeping failed subagent worktree with changes: %s",
                    handle.path,
                )
                return
        else:
            commit_worktree(handle, _commit_message_for(task.prompt))
            summarize_changes(handle, project_root)
            _copy_git_metadata(result, handle)
    except Exception as e:
        logger.error(
            "subagent_git: finalize sub=%d failed: %s",
            handle.sub_idx + 1, e, exc_info=True,
        )
    try:
        cleanup_worktree(project_root, handle)
    except Exception as e:
        logger.warning(
            "subagent_git: cleanup_worktree sub=%d failed: %s", handle.sub_idx + 1, e,
        )


async def run_api_subagents(
    tasks: list,
    proof: str,
    default_provider_id: str,
    default_model_id: str,
    buffers: list[SubagentBuffer],
    status_cb,
    handles: list,
    project_root: str,
    run_dir: Optional[str] = None,
    isolate: bool = True,
) -> list:
    """Запускает субагентов в API-режиме с учётом DAG-зависимостей (depends_on).

    Задачи без зависимостей идут параллельно; зависимые ждут предшественников
    и получают их результаты в промпте. Внутри волны — asyncio.gather.

    tasks: list[SubagentTask]
    handles: list[WorktreeHandle] — по одному на task, в том же порядке.
    Возвращает: list[SubagentResult] в исходном порядке задач.
    """
    from agent.subagent import SubagentResult

    waves, dep_error = _resolve_dependencies(tasks)
    results_by_index: dict[int, SubagentResult] = {}
    _init_progress_log(run_dir, len(tasks))
    if dep_error:
        results = [
            SubagentResult(
                task_index=i, mode=task.mode, response="",
                error=f"Dependency setup failed: {dep_error}",
                phase=getattr(task, "phase", "") or "",
                label=getattr(task, "label", "") or "",
            )
            for i, task in enumerate(tasks)
        ]
        for result, handle in zip(results, handles):
            _finalize_subagent(result, tasks[result.task_index], handle, project_root)
            await _append_progress(run_dir, result, len(tasks))
        return results

    # Ограничитель конкуррентности: при сотнях задач залповый gather упрётся в
    # rate-limit провайдера и пик по диску/FD. Семафор держит не более N
    # субагентов «в полёте» одновременно; остальные ждут слот. 0/отрицательное
    # = без лимита.
    try:
        from config.ui import ui as _ui
        max_conc = int(_ui.get("subagent.max_concurrency", 12))
    except Exception:
        max_conc = 12
    sem = asyncio.Semaphore(max_conc) if max_conc and max_conc > 0 else None

    async def _run_one(runner, task, handle) -> SubagentResult:
        """Запускает одного субагента, финализирует и сразу пишет в progress.md.

        Финализация и запись в лог выполняются ВНУТРИ корутины каждого
        субагента — поэтому завершённый появляется в логе сразу, не дожидаясь
        остальных субагентов своей волны.
        """
        i = runner.index
        try:
            if sem is None:
                raw = await runner.run()
            else:
                async with sem:
                    raw = await runner.run()
            text, iters, err = raw
            result = SubagentResult(
                task_index=i, mode=task.mode, response=text,
                iterations=iters, elapsed=runner.buffer.elapsed if runner.buffer else 0.0,
                error=err,
                model_label=runner.model_id,
                phase=getattr(task, "phase", "") or "",
                label=getattr(task, "label", "") or "",
            )
        except Exception as e:
            result = SubagentResult(
                task_index=i, mode=task.mode, response="",
                error=f"{type(e).__name__}: {e}",
                elapsed=runner.buffer.elapsed if runner.buffer else 0.0,
                model_label=runner.model_id,
                phase=getattr(task, "phase", "") or "",
                label=getattr(task, "label", "") or "",
            )
        _finalize_subagent(result, task, handle, project_root)
        await _append_progress(run_dir, result, len(tasks))
        return result

    for wave in waves:
        coros = []
        for i in wave:
            task = tasks[i]
            preset = None
            preset_name = getattr(task, "preset", None)
            if preset_name:
                try:
                    from agent.agent_presets import load_preset
                    preset = load_preset(preset_name)
                    if preset is None:
                        logger.warning(
                            "subagent: preset '%s' not found (task %d), ignoring",
                            preset_name, i + 1,
                        )
                except Exception:
                    logger.warning("subagent: load_preset failed", exc_info=True)

            # Пресет задаёт model как ДЕФОЛТ — явная model в task переопределяет.
            model_req = getattr(task, "model", None)
            if not model_req and preset and preset.model:
                model_req = preset.model

            provider_id, model_id = resolve_subagent_model(
                model_req,
                default_provider_id,
                default_model_id,
            )
            dep_ctx = _build_dep_context(task, results_by_index)
            runner = _ApiSubagentRunner(
                index=i,
                prompt=task.prompt,
                mode=task.mode,
                provider_id=provider_id,
                model_id=model_id,
                proof=proof,
                buffer=buffers[i] if i < len(buffers) else None,
                status_cb=status_cb,
                handle=handles[i],
                role=getattr(task, "role", None),
                dep_context=dep_ctx,
                preset=preset,
                project_root=project_root,
                isolate=isolate,
            )
            # _run_one финализирует и пишет в progress.md внутри себя.
            coros.append(_run_one(runner, task, handles[i]))

        # Внутри волны субагенты идут параллельно; каждый сам пишет
        # себя в progress.md сразу по завершению (не ждём всю волну).
        wave_results = await asyncio.gather(*coros, return_exceptions=True)
        for raw in wave_results:
            if isinstance(raw, Exception):
                logger.error("subagent: wave coro crashed: %s", raw, exc_info=raw)
                continue
            results_by_index[raw.task_index] = raw

    return [
        results_by_index.get(i) or SubagentResult(
            task_index=i, mode=tasks[i].mode, response="",
            error="Subagent produced no result.",
            phase=getattr(tasks[i], "phase", "") or "",
            label=getattr(tasks[i], "label", "") or "",
        )
        for i in range(len(tasks))
    ]