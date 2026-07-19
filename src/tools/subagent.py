"""Tool handler для subagent (API-only)."""

import asyncio
import logging

from tools.models import ToolCall, ToolResult
from tools.subagent_specs import build_subagent_task_specs

logger = logging.getLogger(__name__)

# Intentional per-process state: set once via set_subagent_context() at CLI
# startup and read by execute_subagent(). Kept as module globals (not a class)
# because there is exactly one orchestrator context per process.
_model: str = ""
_working_dir: str = ""
_event_handler = None


def set_subagent_context(model: str, working_dir: str, event_handler=None):
    global _model, _working_dir, _event_handler
    _model = model
    _working_dir = working_dir
    _event_handler = event_handler


def _task_from_spec(spec):
    from agent.subagent import SubagentTask

    kwargs = {
        "prompt": spec["prompt"],
        "mode": "agent",
        "model": spec.get("model"),
        "role": spec.get("role"),
        "preset": spec.get("preset"),
        "depends_on": list(spec.get("depends_on") or []),
    }
    for key in ("phase", "label"):
        if spec.get(key):
            kwargs[key] = spec[key]
    return SubagentTask(**kwargs)


def execute_subagent(call: ToolCall) -> ToolResult:
    args = call.args or {}
    isolate = bool(args.get("isolate", False))
    task_specs, summary = build_subagent_task_specs(args)

    from agent.subagent import SubagentOrchestrator, format_subagent_results

    tasks = [_task_from_spec(spec) for spec in task_specs]

    if not tasks:
        return ToolResult(
            name="subagent",
            status="error",
            output=(
                "No valid subagent tasks provided. Use prompt, tasks[], "
                "items+stages, or phases[]."
            ),
            exit_code=1,
            command=call.command,
        )

    def _status_callback(index: int, msg: str):
        if _event_handler:
            _event_handler.on_status(
                f"\U0001f916 Subagent {index + 1}: {msg}",
                level="info",
            )

    orchestrator = SubagentOrchestrator(
        model=_model,
        working_dir=_working_dir,
        on_status=_status_callback,
        isolate=isolate,
    )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_new_loop, orchestrator, tasks)
            # Без жёсткого таймаута: при сотнях задач в волнах фиксированные
            # 600s легко превышаются, а future.result(timeout=...) лишь бросает
            # TimeoutError — поток с зависшим event loop остаётся жить и блокирует
            # выход пула. Лимиты времени обеспечиваются на уровне самих
            # субагентов (итерации/таймауты провайдера), а не здесь.
            results = future.result()
    else:
        results = asyncio.run(orchestrator.run(tasks))

    output = f"Subagent run {summary}\n\n" + format_subagent_results(results, run_dir=orchestrator.run_dir)
    has_errors = any(r.error for r in results)

    return ToolResult(
        name="subagent",
        status="error" if has_errors else "ok",
        output=output,
        exit_code=1 if has_errors else 0,
        command=call.command,
    )


def _run_in_new_loop(orchestrator, tasks):
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(orchestrator.run(tasks))
    finally:
        new_loop.close()
