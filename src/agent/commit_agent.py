"""Фоновый commit-агент.

Запускается из slash-команды /commit как отдельная asyncio-задача: анализирует
незакоммиченные изменения в рабочем дереве и делает один/несколько осмысленных
коммитов на текущей ветке. Работает в ИЗОЛИРОВАННОЙ ApiSession (не трогает
глобальную сессию интерактивного агента), поэтому пользователь может параллельно
давать новые задачи основному агенту.

Без Rich Live / prompt_toolkit вывода: стримит только короткие статусы через
on_status (печатаются над активным prompt'ом благодаря patch_stdout в main loop).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import tools
from agent.messages import gather_proof
from agent.sanitizer import sanitize_response
from apis._retry import with_throttle_retry
from apis.agent_adapter import (
    ApiSession,
    _content_to_text,
    _ensure_tool_call_ids,
)
from apis.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from apis.registry import get_provider
from apis.tool_schemas import get_tool_schemas
from system_prompt import build_system_prompt, build_tool_results
from tools import parse_tool_calls, strip_tool_calls
from tools._paths import use_working_dir
from tools.registry import execute_call

logger = logging.getLogger(__name__)

MAX_COMMIT_ITERATIONS = 40


def _native_tool_calls_to_calls(native_calls: list[dict]) -> list[tools.ToolCall]:
    calls = []
    for call in native_calls:
        name = call.get("name") or "shell"
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        command = str(args.get("command") or "shell") if name == "shell" else name
        calls.append(tools.ToolCall(command=command, tool_name=name, args=args, raw=""))
    return calls

_COMMIT_MODE_BLOCK = (
    "\n\n━━━ COMMIT AGENT MODE ━━━\n"
    "You are a focused background COMMIT agent. Your ONLY job: turn the current "
    "uncommitted work in the working tree into one or more meaningful, atomic git "
    "commits on the CURRENT branch.\n\n"
    "Procedure:\n"
    "1. Inspect state: `git status --porcelain`, `git diff`, `git diff --staged`, "
    "`git log --oneline -5` (read the current branch from `git rev-parse "
    "--abbrev-ref HEAD` — NEVER assume main/master).\n"
    "2. Group related changes into separate commits when they are logically "
    "distinct; otherwise a single commit is fine. Stage with `git add <paths>` "
    "(specific paths, not blindly `-A` if changes are mixed).\n"
    "3. Write clear, concise commit messages (imperative mood, summary line "
    "<=72 chars, optional body explaining WHY).\n"
    "4. Commit each group with `git commit -m \"...\"`.\n\n"
    "HARD RULES:\n"
    "- Use ONLY git via the shell tool. Do NOT edit/create/delete project files, "
    "do NOT refactor, do NOT run tests.\n"
    "- NEVER push, force, reset --hard, rebase, checkout other branches, or amend "
    "existing pushed commits. Commit on the current branch only.\n"
    "- If there is nothing to commit (clean tree), do nothing and say so.\n"
    "- Never ask the user questions — decide and act autonomously.\n\n"
    "FINAL ANSWER (text only, no tool call, when done): list the commits you made "
    "(short SHA + message), or state that the tree was already clean. Be terse.\n"
)


def _build_task_prompt(hint: str) -> str:
    base = (
        "Commit the current uncommitted work in this repository into one or more "
        "meaningful commits on the current branch, following the COMMIT AGENT MODE "
        "procedure."
    )
    if hint and hint.strip():
        base += f"\n\nAdditional instructions from the user: {hint.strip()}"
    return base


async def _call_model(session: ApiSession, provider_id: str, model_id: str,
                      use_native: bool, schemas: list[dict]) -> tuple[str, list[dict]]:
    llm = get_provider(provider_id, model_id)
    want_tools = use_native and bool(schemas)
    bound_ok = False
    if want_tools:
        try:
            if hasattr(llm, "streaming"):
                llm.streaming = False
        except Exception:
            logger.debug("commit-agent: set streaming=False failed", exc_info=True)
        try:
            llm = llm.bind_tools(schemas, tool_choice="auto")
            bound_ok = True
        except Exception as e:
            logger.warning("commit-agent bind_tools failed, fenced fallback: %s", e)

    result = await with_throttle_retry(lambda: llm.ainvoke(session.messages))
    raw_text = _content_to_text(getattr(result, "content", result))
    tool_calls = list(getattr(result, "tool_calls", []) or [])
    if tool_calls:
        tool_calls = _ensure_tool_call_ids(tool_calls)
    if want_tools and not bound_ok:
        # провайдер не умеет native — модель будет звать через fenced в raw_text
        tool_calls = []
    return raw_text, tool_calls


def _execute(calls: list, working_dir: str) -> list[tools.ToolResult]:
    """Исполняет tool calls в working_dir. Разрешён только shell (git)."""
    results = []
    with use_working_dir(working_dir):
        for call in calls:
            if call.tool_name != "shell":
                results.append(tools.ToolResult(
                    name=call.tool_name,
                    status="error",
                    output=(
                        f"Tool '{call.tool_name}' is not available to the commit agent. "
                        "Use only the shell tool with git commands."
                    ),
                    exit_code=1,
                    command=call.command,
                ))
                continue
            try:
                r = execute_call(call)
            except Exception as e:
                logger.error("commit-agent tool %s crashed: %s", call.tool_name, e, exc_info=True)
                r = tools.ToolResult(
                    name=call.tool_name, status="error",
                    output=f"Tool crashed: {type(e).__name__}: {e}",
                    exit_code=1, command=call.command,
                )
            results.append(r)
    return results


def _truncate(text: str, limit: int = 20000) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [{len(text)} chars, truncated] ...\n" + text[-half:]


def _build_repeat_tool_notice(last_tool_name: str | None, calls: list) -> tuple[str, str | None]:
    if not calls:
        return "", None
    tool_name = calls[0].tool_name
    if tool_name != last_tool_name:
        return "", tool_name
    return (
        "[repeat-tool notice]\n"
        f"You called `{tool_name}` in two consecutive tool rounds. "
        "Before calling it again, check whether the previous result already "
        "answers the task, or explain why repeating the same tool is necessary.",
        tool_name,
    )


async def run_commit_agent(
    provider_id: str,
    model_id: str,
    working_dir: str,
    hint: str = "",
    on_status: Callable[[str], None] | None = None,
) -> str:
    """Запускает фоновый commit-агентный цикл. Возвращает финальный текст."""
    def _status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                logger.debug("commit-agent on_status failed", exc_info=True)

    logger.info("commit-agent start: provider=%s model=%s wd=%s", provider_id, model_id, working_dir)
    session = ApiSession(provider_id, model_id)
    use_native = bool(getattr(session, "use_native_tools", False))

    proof = await gather_proof(working_dir)
    system = build_system_prompt(
        proof=proof, mode="agent", working_dir=working_dir,
        think_enabled=False, native_tools=use_native,
    ) + _COMMIT_MODE_BLOCK

    # Только shell нужен commit-агенту.
    schemas = [
        s for s in get_tool_schemas("agent")
        if s.get("function", {}).get("name") == "shell"
    ]

    session.messages.append(SystemMessage(content=system))
    session.messages.append(HumanMessage(content=_build_task_prompt(hint)))

    raw_text = ""
    last_tool_name: str | None = None
    for i in range(MAX_COMMIT_ITERATIONS):
        _status(f"iteration {i + 1}")
        raw_text, native_calls = await _call_model(session, provider_id, model_id, use_native, schemas)
        raw_text = sanitize_response(raw_text)

        kwargs = {"content": raw_text}
        if native_calls and use_native:
            kwargs["tool_calls"] = native_calls
        session.messages.append(AIMessage(**kwargs))

        if native_calls:
            calls = _native_tool_calls_to_calls(native_calls)
        else:
            calls = parse_tool_calls(raw_text)
        calls = [c for c in calls if c.tool_name != "think"]

        repeat_tool_notice, last_tool_name = _build_repeat_tool_notice(last_tool_name, calls)
        if not calls:
            return strip_tool_calls(raw_text).strip()

        results = _execute(calls, working_dir)

        if native_calls:
            by_name: dict = {}
            for r in results:
                by_name.setdefault(r.name, []).append(r)
            for tc in native_calls:
                name = tc.get("name") or "shell"
                bucket = by_name.get(name) or []
                r = bucket.pop(0) if bucket else None
                content = _truncate(r.output or "") if r else f"No result for {name}."
                if r and r.status == "error":
                    content = f"[error exit={r.exit_code}]\n{content}"
                session.messages.append(ToolMessage(
                    content=content, tool_call_id=tc.get("id") or "", name=name,
                ))
            if repeat_tool_notice:
                session.messages.append(HumanMessage(content=repeat_tool_notice))
        else:
            result_dicts = []
            for r in results:
                d = r.to_dict()
                d["output"] = _truncate(d.get("output") or "")
                result_dicts.append(d)
            result_msg = build_tool_results(result_dicts)
            if repeat_tool_notice:
                result_msg += "\n\n" + repeat_tool_notice
            session.messages.append(HumanMessage(content=result_msg))

    logger.warning("commit-agent: iteration limit reached")
    return strip_tool_calls(raw_text).strip() + "\n\n[commit agent iteration limit]"
