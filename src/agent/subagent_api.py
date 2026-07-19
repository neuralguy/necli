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

import tools
from agent.sanitizer import sanitize_response
from agent.subagent_git import (
    WorktreeHandle,
    cleanup_worktree,
    commit_worktree,
    summarize_changes,
    summarize_worktree_changes,
)
from agent.subagent_render import SubagentBuffer
from apis._retry import stream_with_throttle_retry, with_throttle_retry
from apis.agent_adapter import (
    ApiSession,
    _content_to_text,
    _ensure_tool_call_ids,
    _tool_calls_to_text_blocks,
)
from apis.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from apis.registry import get_provider, resolve_api_model
from apis.tool_schemas import get_tool_schemas
from system_prompt import build_system_prompt, build_tool_results
from tools import parse_tool_calls, strip_tool_calls
from tools._paths import get_working_dir, use_working_dir
from tools.registry import execute_call

logger = logging.getLogger(__name__)

MAX_SUBAGENT_ITERATIONS = 200

# Потолок АКТИВНОГО контекста (input одного вызова) субагента. Бэкстоп от
# runaway-петель verify/polish: «проверь → поправь → перечитай файл → снова
# проверь» раздувает контекст одного вызова, перечитывая большие файлы по кругу;
# прунер вытесняет старое, но если петля тащит всё больше — input улетает вверх.
# При превышении субагент останавливается с тем, что есть (graceful). Это НЕ
# кумулятив по итерациям (тот рос бы O(N²) и ложно стопил нормальную длинную
# работу) — длину ограничивает MAX_SUBAGENT_ITERATIONS. 1M токенов активного
# контекста — заведомо аномалия для фокусной задачи субагента.
MAX_SUBAGENT_CONTEXT_TOKENS = 1_000_000

# Таймаут на ОДИН вызов модели субагентом. Прокси (onlysq) умеет зависать на
# стриме (в логах ответы по 38с и дольше); без таймаута повисший ainvoke/astream
# блокирует субагента — и весь воркфлоу/пул — навсегда. При срабатывании итерация
# завершается с ошибкой, цикл идёт дальше (или штатно упирается в лимит итераций).
MODEL_CALL_TIMEOUT_SEC = 240.0

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


def _normalize_role(role: str | None) -> str | None:
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
_BLOCKED_FOR_SUBAGENTS = frozenset({"poll", "subagent"})


def resolve_subagent_model(
    requested: str | None,
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
        buffer: SubagentBuffer | None,
        status_cb,
        handle: WorktreeHandle,
        role: str | None = None,
        dep_context: str = "",
        preset=None,
        project_root: str = "",
        isolate: bool = True,
        wave_size: int = 1,
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
        # Сколько субагентов работает ОДНОВРЕМЕННО в этой волне (включая себя).
        # 1 → пиров нет, ждать некого: критично против sleep-поллинга «соседа».
        self.wave_size = max(1, int(wave_size or 1))
        # АКТИВНЫЙ контекст последнего вызова модели (input_tokens последнего
        # обмена). Это правильный сигнал runaway-петли: каждый вызов шлёт весь
        # растущий контекст, и если прунер не справляется (verify/polish крутит
        # перечитывание больших файлов), input одного вызова улетает за потолок
        # окна. СУММИРОВАТЬ input по итерациям нельзя — это O(N²) и ложно стопит
        # нормальную длинную работу. Число итераций ограничено отдельно
        # (MAX_SUBAGENT_ITERATIONS). Считаем независимо от buffer (на tool-пути
        # buffer=None).
        self._last_input_tokens = 0
        # Кумулятив (input+output по всем вызовам) — только для лога/справки.
        self._spent_tokens = 0

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
        # Субагент не гейтит инструменты скиллами: он короткоживущий и фокусный,
        # а его mode-block прямо обещает доступность web_search и пр. Даём ему
        # полный набор (все гейтящие скиллы считаем «активными»).
        from skills.registry import SKILL_TOOLS as _SK
        all_skills = set(_SK)
        base = build_system_prompt(
            proof=proof,
            mode="agent",
            working_dir=self.working_dir,
            think_enabled=False,
            native_tools=self.use_native,
            for_subagent=True,
            active_skills=all_skills,
        )
        mode_block = (
            "\n\n━━━ SUBAGENT MODE: AGENT ━━━\n"
            "You are an autonomous subagent. Execute the task to FULL completion and "
            "return a concrete result. All tools are available EXCEPT poll and subagent "
            "(web_search IS available — use it for any real-time/online info). "
            "NEVER ask the user questions — decide and act.\n"
            f"{self._workspace_prompt()}"
            "CONTEXT DISCIPLINE (your context is small — keep it lean, every token counts):\n"
            "  - LOCATE, then read NARROW. Never open a file whole to find something. For a symbol "
            "(function/class/method/variable) the FIRST tool is LSP (lsp_definition/references/hover); "
            "for text it's grep_files. Then read a TARGETED window (≈±60 lines) around the hit — not the "
            "entire file. Read a file in full ONLY if it's genuinely small (≲200 lines) or you truly "
            "need all of it. Dragging a 1000-line file into context to touch one function is exactly the "
            "waste that kills a subagent — a few grep/LSP calls plus narrow reads cost a fraction of it.\n"
            "  - You ALREADY KNOW the content you write/patch — do NOT re-read a file right after "
            "editing it just to 'check'. The edit either applied or errored; trust the result.\n"
            "  - Read each needed range ONCE up front (batch them in one read_files call). Don't re-read "
            "the same file across iterations 'to be sure'.\n"
            "  - VERIFY ONCE at the end, not after every change: make all edits, then run the check "
            "(test/grep/build) a single time. Fix only what it surfaces.\n"
            "  - This is a bounded task, NOT endless polishing. When it works and meets the brief, STOP "
            "and give the final answer. Do not keep re-reading and re-tweaking for marginal gains.\n"
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

        # ⛔ Анти-sleep: субагент НИКОГДА не должен спать/поллить в ожидании
        # соседа. Файлы ниже — справка для ОДНОКРАТНОГО чтения, не канал ожидания.
        mode_block += (
            "\n━━━ NEVER WAIT, NEVER SLEEP FOR A PEER ━━━\n"
            "You CANNOT wait for another subagent. Same-wave peers run in parallel and their files may "
            "not exist yet; `sleep N && cat <peer-file>` / `sleep; if [ -f ... ]` / any poll-retry loop "
            "for a sibling's output is ALWAYS A BUG — it just burns your wall-clock and the file still "
            "won't be there. If you NEED another agent's output before you can start, that is a "
            "dependency the orchestrator must model with depends_on (its result is then injected into "
            "your prompt under RESULTS FROM DEPENDENCY SUBAGENTS) — it is NOT something you sleep for. "
            "`sleep` is allowed ONLY for a real local reason (wait a few seconds for a server you just "
            "started before curl-ing it; let a process you killed die) — never to wait on a peer.\n"
        )
        if self.wave_size <= 1:
            mode_block += (
                "You are the ONLY subagent in this wave — there are NO peers running alongside you. "
                "Nothing will appear in any shared/progress file from a sibling. Do your task and "
                "finish; do not look for or wait on work from others.\n"
            )

        import os as _os
        progress_path = _os.path.join(
            _os.path.dirname(self.working_dir.rstrip("/")), "progress.md",
        )
        mode_block += (
            "\n━━━ PEER PROGRESS LOG (read-once reference, never poll) ━━━\n"
            "A log of THIS run's subagents is at:\n"
            f"  {progress_path}\n"
            "Each peer is appended the moment it FINISHES. Read it AT MOST ONCE if you genuinely need "
            "to know what already completed — then act on whatever is there. Do NOT re-read it in a "
            "loop and NEVER sleep waiting for an entry to appear. It lives OUTSIDE your worktree and "
            "is NOT committed.\n"
        )

        scratch = _read_scratchpad(self.working_dir)
        mode_block += (
            "\n━━━ SHARED SCRATCHPAD (read-once reference, never poll) ━━━\n"
            "A shared notes file for this run is at:\n"
            f"  {_shared_scratchpad_path(self.working_dir)}\n"
            "Read it ONCE for contracts/interfaces already agreed on, then proceed. Append (never "
            "overwrite) your own decisions peers may need. Treat a missing entry as 'not decided yet, "
            "decide it yourself' — never sleep waiting for one. OUTSIDE your worktree, NOT committed.\n"
        )
        if scratch:
            mode_block += f"Current scratchpad content:\n{scratch}\n"

        # text-mode формат теперь внутри build_system_prompt (native_tools).
        return base + mode_block

    def _tools_schema(self) -> list[dict]:
        # Субагент не гейтит инструменты скиллами (см. _build_system_prompt) —
        # передаём все гейтящие скиллы как активные, гасим только запрещённые.
        from skills.registry import SKILL_TOOLS as _SK
        schemas = get_tool_schemas("agent", set(_SK))
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

    def _pruned_messages(self) -> list:
        """Контекст для отправки модели с вытеснением старых read/tool-выводов.

        Главный цикл (agent_adapter) прунит контекст перед каждым вызовом, а
        субагент раньше слал сырой self.session.messages — он копился линейно
        (54 tool-call = 350k токенов). Тот же prune_messages: не мутирует вход,
        вытесняет устаревшие/крупные/древние чтения, дедуплицирует пути.
        """
        from apis._context_pruner import prune_messages
        messages, stats = prune_messages(self.session.messages)
        if stats["pruned_blocks"]:
            logger.info(
                "subagent %d context pruner: evicted %d block(s), saved ~%d chars",
                self.index + 1, stats["pruned_blocks"], stats["saved_chars"],
            )
        return messages

    def _track_usage(self, usage) -> None:
        """Копит токены раннера из usage_metadata и обновляет buffer, если он есть.

        Источник истины для бюджет-гарда — self._spent_tokens (работает и когда
        buffer=None, т.е. на основном tool-пути).
        """
        if self.buffer:
            self.buffer.on_usage(usage)
        if isinstance(usage, dict):
            it = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            ot = usage.get("output_tokens") or usage.get("completion_tokens") or 0
            total = usage.get("total_tokens")
            try:
                self._last_input_tokens = int(it)
                self._spent_tokens += int(total) if total else int(it) + int(ot)
            except (TypeError, ValueError):
                pass

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

        # Прунинг ПЕРЕД каждым вызовом — как в главном цикле. Отправляем
        # пруненую копию, не сырой self.session.messages.
        msgs = self._pruned_messages()

        raw_text = ""
        tool_calls: list[dict] = []

        if use_tools:
            result = await with_throttle_retry(lambda: llm.ainvoke(msgs))
            raw_text = _content_to_text(getattr(result, "content", result))
            tool_calls = list(getattr(result, "tool_calls", []) or [])
            tool_calls = _ensure_tool_call_ids(tool_calls)
            self._track_usage(getattr(result, "usage_metadata", None))
            if on_chunk is not None:
                if raw_text:
                    on_chunk(raw_text)
                if tool_calls:
                    on_chunk(raw_text + _tool_calls_to_text_blocks(tool_calls))
        elif on_chunk is not None:
            final_chunk = await stream_with_throttle_retry(
                lambda: llm.astream(msgs),
                on_chunk,
                on_tool_chunk=lambda c: None,
            )
            raw_text = _content_to_text(getattr(final_chunk, "content", ""))
            tool_calls = list(getattr(final_chunk, "tool_calls", []) or [])
            self._track_usage(getattr(final_chunk, "usage_metadata", None))
            if tool_calls:
                tool_calls = _ensure_tool_call_ids(tool_calls)
                on_chunk(raw_text + _tool_calls_to_text_blocks(tool_calls))
        else:
            result = await with_throttle_retry(lambda: llm.ainvoke(msgs))
            raw_text = _content_to_text(getattr(result, "content", result))
            tool_calls = list(getattr(result, "tool_calls", []) or [])
            tool_calls = _ensure_tool_call_ids(tool_calls)
            self._track_usage(getattr(result, "usage_metadata", None))

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
                    self.buffer.on_tool_done(
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

    async def run(self) -> tuple[str, int, str | None]:
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
                # Context-size backstop: если АКТИВНЫЙ контекст одного вызова
                # (input последнего обмена) раздулся за потолок — это runaway-петля
                # read/patch/re-read, которую прунер не смог удержать. Останавливаемся
                # с тем, что есть. Сравниваем активный контекст, НЕ кумулятив по
                # итерациям (тот рос бы O(N²) и ложно стопил нормальную длинную
                # работу). Длину ограничивает MAX_SUBAGENT_ITERATIONS.
                ctx = self._last_input_tokens
                if ctx > MAX_SUBAGENT_CONTEXT_TOKENS:
                    logger.warning(
                        "Subagent %s context too large (%d > %d) at iter %d "
                        "(cumulative billed: %d) — stopping",
                        self.index, ctx, MAX_SUBAGENT_CONTEXT_TOKENS,
                        iterations, self._spent_tokens,
                    )
                    final = strip_tool_calls(raw_text).strip()
                    final = (final + "\n\n[Subagent stopped: context size limit reached]").strip()
                    if self.buffer:
                        self.buffer.on_done(final)
                    # Контекст переполнен = РАБОТА, скорее всего, НЕ ДОВЕДЕНА до конца.
                    # Возвращаем error (а не None), чтобы главный агент
                    # узнали о неполноте, а не считали это полным успехом. Сделанный
                    # текст сохраняется в final — он не теряется.
                    return final, iterations + 1, "stopped: context size limit reached (work likely incomplete)"

                self.status_cb(self.index, f"Iteration {iterations + 1}")
                if self.buffer:
                    self.buffer.streaming_text = ""
                    self.buffer.on_iteration(iterations)

                try:
                    raw_text, native_tool_calls = await asyncio.wait_for(
                        self._call_model(), timeout=MODEL_CALL_TIMEOUT_SEC,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    logger.warning(
                        "Subagent %d model call timed out (%.0fs) at iter %d",
                        self.index + 1, MODEL_CALL_TIMEOUT_SEC, iterations,
                    )
                    # Повисший провайдер: не зависаем навсегда. Подсказываем
                    # модели продолжить на следующей итерации; если повторяется —
                    # упрёмся в лимит итераций и завершимся с тем, что есть.
                    self.session.messages.append(HumanMessage(
                        content="(previous model call timed out — continue concisely)",
                    ))
                    continue
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
                all_calls = native_as_calls if native_tool_calls else text_calls

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

            # Лимит итераций исчерпан — как и бюджет, это сигнал неполноты:
            # помечаем error, чтобы главный агент знал (текст сохранён).
            final = strip_tool_calls(raw_text).strip() + "\n\n[Subagent iteration limit]"
            if self.buffer:
                self.buffer.on_done(final)
            return final, iterations + 1, "stopped: iteration limit reached (work likely incomplete)"

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


def _progress_log_path(run_dir: str | None) -> str | None:
    import os
    if not run_dir:
        return None
    return os.path.join(run_dir, "progress.md")


def _init_progress_log(run_dir: str | None, total: int) -> None:
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


async def _append_progress(run_dir: str | None, result, total: int) -> None:
    """Дописывает запись о завершившемся субагенте в progress.md."""
    path = _progress_log_path(run_dir)
    if not path:
        return
    n = result.task_index + 1
    phase = f" [{result.phase}]" if getattr(result, "phase", "") else ""
    label = f" {result.label}" if getattr(result, "label", "") else ""
    if result.error:
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
                        block.append(f"  {f}")  # noqa: PERF401
                if getattr(result, "diff_stat", ""):
                    block.append("")
                    block.append(result.diff_stat)
            else:
                block.append("no changes")
        block.append("")
    else:
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
            with open(path, "a", encoding="utf-8") as fh:  # noqa: ASYNC230
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
    run_dir: str | None = None,
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
        for result, handle in zip(results, handles, strict=False):
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
                wave_size=len(wave),
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
