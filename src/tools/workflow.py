"""Tool handler for Python workflows."""

import asyncio

from tools.models import ToolCall, ToolResult


def execute_workflow(call: ToolCall) -> ToolResult:
    args = call.args or {}
    try:
        from tools.subagent import get_subagent_context
        model, working_dir, _event_handler = get_subagent_context()
        from workflows.runner import WorkflowRunner

        runner = WorkflowRunner(
            model=model,
            working_dir=working_dir,
            isolate=bool(args.get("isolate", True)),
            resume_from_run_id=str(args.get("resume_from_run_id") or ""),
            cache=bool(args.get("cache", True)),
            fail_fast=bool(args.get("fail_fast", False)),
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                output = pool.submit(_run_in_new_loop, runner, args).result()
        else:
            output = asyncio.run(runner.run(args))

        return ToolResult(
            name="workflow",
            status="ok",
            output=output,
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        return ToolResult(
            name="workflow",
            status="error",
            output=f"Workflow failed: {type(e).__name__}: {e}",
            exit_code=1,
            command=call.command,
        )


def _run_in_new_loop(runner, args):
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(runner.run(args))
    finally:
        new_loop.close()