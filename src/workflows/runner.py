"""Python workflow runner built on top of existing subagents."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import subprocess
from typing import Any

from agent.subagent import SubagentOrchestrator, SubagentTask
from agent.subagent_render import SubagentBuffer
from workflows.render import WorkflowTracker, short_agent_label
from workflows.specs import (
    WorkflowAgentState,
    WorkflowPhaseState,
    WorkflowRunState,
    new_run_id,
    save_json,
    utc_now,
)


class WorkflowAgentCall:
    def __init__(self, ctx: "WorkflowContext", prompt: str, opts: dict[str, Any]):
        self.ctx = ctx
        self.prompt = str(prompt or "")
        self.opts = dict(opts or {})

    def __await__(self):
        return self.ctx._run_agent_calls([self]).__await__()


class WorkflowContext:
    def __init__(self, runner: "WorkflowRunner"):
        self.runner = runner
        self.current_phase: WorkflowPhaseState | None = None

    def phase(self, title: str, detail: str = "") -> WorkflowPhaseState:
        title = str(title or "Phase")
        if self.current_phase and self.current_phase.status == "running":
            self.current_phase.status = "done"
            self.current_phase.finished_at = utc_now()

        phase = None
        for candidate in self.runner.state.phases:
            if candidate.title == title and candidate.status == "pending":
                phase = candidate
                break
        if phase is None:
            phase = WorkflowPhaseState(
                id=f"phase-{len(self.runner.state.phases) + 1}",
                title=title,
                detail=str(detail or ""),
            )
            self.runner.state.phases.append(phase)

        phase.detail = str(detail or phase.detail or "")
        phase.status = "running"
        phase.started_at = phase.started_at or utc_now()
        self.current_phase = phase
        self.runner.save_state()
        self.runner._select_phase(phase)
        return phase

    def log(self, text: str) -> None:
        if self.current_phase is None:
            self.phase("Workflow")
        self.current_phase.logs.append(str(text or ""))
        self.runner.save_state()

    def agent(self, prompt: str, opts: dict[str, Any] | None = None, **kwargs: Any) -> WorkflowAgentCall:
        merged = dict(opts or {})
        merged.update(kwargs)
        return WorkflowAgentCall(self, prompt, merged)

    def verify(
        self,
        original_request: str,
        evidence: Any = "",
        checks: list[str] | None = None,
        label: str = "verify",
        **kwargs: Any,
    ) -> WorkflowAgentCall:
        checks_text = "\n".join(f"- {c}" for c in (checks or [])) or "- Run the relevant checks you identify."
        prompt = (
            "Independently verify the work against the ORIGINAL user request. "
            "Prove behavior, do not rubber-stamp implementation claims.\n\n"
            f"ORIGINAL REQUEST:\n{original_request}\n\n"
            f"EVIDENCE / IMPLEMENTATION CONTEXT:\n{evidence}\n\n"
            f"CHECKS TO RUN OR ASSESS:\n{checks_text}\n\n"
            "Return exactly these sections:\n"
            "VERDICT: PASS|FAIL|PARTIAL\n"
            "EVIDENCE:\n- commands/checks run and observed results\n"
            "FINDINGS:\n- file:line — issue, or '(none)'\n"
            "NEXT_FIX:\n- concrete next fix if verdict is not PASS, or '(none)'"
        )
        opts = {"label": label, "role": "reviewer"}
        opts.update(kwargs)
        return self.agent(prompt, opts)

    async def loop_until_pass(
        self,
        implement: Any,
        verify: Any,
        max_rounds: int = 3,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        findings: Any = None
        for round_index in range(1, max(1, int(max_rounds)) + 1):
            impl_call = implement(findings, round_index) if callable(implement) else implement
            if isinstance(impl_call, WorkflowAgentCall) or inspect.isawaitable(impl_call):
                implementation = await impl_call
            else:
                implementation = impl_call
            verify_call = verify(implementation, round_index) if callable(verify) else verify
            if isinstance(verify_call, WorkflowAgentCall) or inspect.isawaitable(verify_call):
                verification = await verify_call
            else:
                verification = verify_call
            verdict = _extract_verdict(verification)
            attempt = {
                "round": round_index,
                "implementation": implementation,
                "verification": verification,
                "verdict": verdict,
            }
            attempts.append(attempt)
            if verdict == "PASS":
                return {"verdict": "PASS", "attempts": attempts}
            findings = verification
        return {"verdict": attempts[-1]["verdict"] if attempts else "PARTIAL", "attempts": attempts}

    async def parallel(self, calls: list[Any]) -> list[dict[str, Any] | None]:
        agent_calls: list[WorkflowAgentCall] = []
        other = []
        for item in calls or []:
            value = item() if callable(item) else item
            if isinstance(value, WorkflowAgentCall):
                agent_calls.append(value)
            else:
                other.append(value)

        out: list[dict[str, Any] | None] = []
        if agent_calls:
            out.extend(await self._run_agent_calls(agent_calls))
        if other:
            gathered = await asyncio.gather(
                *[v if inspect.isawaitable(v) else _const(v) for v in other],
                return_exceptions=True,
            )
            for value in gathered:
                out.append(None if isinstance(value, Exception) else value)
        return out

    async def pipeline(self, items: list[Any], *stages: Any) -> list[Any]:
        async def run_item(item: Any, index: int) -> Any:
            value = item
            for stage_index, stage in enumerate(stages, start=1):
                if callable(stage):
                    value = stage(value, index, stage_index)
                if isinstance(value, WorkflowAgentCall):
                    value = await value
                elif inspect.isawaitable(value):
                    value = await value
            return value

        return await asyncio.gather(
            *[run_item(item, i) for i, item in enumerate(items or [], start=1)]
        )

    async def _run_agent_calls(self, calls: list[WorkflowAgentCall]) -> list[dict[str, Any] | None]:
        if self.current_phase is None:
            self.phase("Workflow")
        return await self.runner.run_agent_calls(self.current_phase, calls)


async def _const(value: Any) -> Any:
    return value


class WorkflowRunner:
    def __init__(
        self,
        model: str,
        working_dir: str,
        isolate: bool = True,
        resume_from_run_id: str = "",
        cache: bool = True,
        fail_fast: bool = False,
    ):
        self.model = model
        self.working_dir = working_dir
        self.isolate = isolate
        self.resume_from_run_id = resume_from_run_id or ""
        self.cache = cache
        self.fail_fast = fail_fast
        self.run_dir = ""
        self.state: WorkflowRunState | None = None
        self._cache_by_key: dict[str, dict[str, Any]] = {}
        self._buffers_by_agent_id: dict[str, SubagentBuffer] = {}
        self._tracker: WorkflowTracker | None = None

    def _init_state(self, meta: dict[str, Any]) -> None:
        name = str(meta.get("name") or "workflow")
        run_id = new_run_id(name)
        self.run_dir = os.path.join(self.working_dir, ".data", "workflow_runs", run_id)
        self.state = WorkflowRunState(
            id=run_id,
            name=name,
            description=str(meta.get("description") or ""),
            status="running",
            run_dir=self.run_dir,
        )
        os.makedirs(self.run_dir, exist_ok=True)
        save_json(os.path.join(self.run_dir, "meta.json"), meta)
        self._load_resume_cache()
        self.save_state()

    def save_state(self) -> None:
        if self.state:
            save_json(os.path.join(self.run_dir, "state.json"), self.state.to_dict())

    def _load_resume_cache(self) -> None:
        if not self.resume_from_run_id or not self.cache:
            return
        state_path = os.path.join(
            self.working_dir, ".data", "workflow_runs", self.resume_from_run_id, "state.json",
        )
        try:
            with open(state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise FileNotFoundError(f"resume workflow run not found: {self.resume_from_run_id}")
        except json.JSONDecodeError as e:
            raise ValueError(f"resume workflow state is invalid JSON: {e}") from e
        for phase in data.get("phases") or []:
            for agent in phase.get("agents") or []:
                key = agent.get("cache_key")
                result = agent.get("result")
                if key and isinstance(result, dict) and agent.get("status") == "done":
                    self._cache_by_key[key] = result

    async def run(self, args: dict[str, Any]) -> str:
        self.cache = bool(args.get("cache", self.cache))
        self.fail_fast = bool(args.get("fail_fast", self.fail_fast))
        if args.get("resume_from_run_id"):
            self.resume_from_run_id = str(args.get("resume_from_run_id") or "")
        meta = self._meta_from_args(args)
        self._init_state(meta)
        self._predeclare_inline_phases(args)
        self._tracker = WorkflowTracker(self.state, self._buffers_by_agent_id)
        self._tracker.start()
        ctx = WorkflowContext(self)

        try:
            has_inline_phases = isinstance(args.get("phases"), list) and bool(args.get("phases"))
            if args.get("script") or args.get("path") or (args.get("name") and not has_inline_phases):
                result = await self._run_python_workflow(ctx, args)
            else:
                result = await self._run_inline_phases(ctx, args)
            if ctx.current_phase and ctx.current_phase.status == "running":
                ctx.current_phase.status = "done"
                ctx.current_phase.finished_at = utc_now()
            self.state.status = "completed"
            self.state.finished_at = utc_now()
            self.state.result = result
            self.save_state()
            save_json(os.path.join(self.run_dir, "result.json"), result)
            return self._format_output()
        except BaseException as e:
            # Ловим BaseException, а не только Exception: KeyboardInterrupt (Ctrl-C)
            # и asyncio.CancelledError (отмена по таймауту) — это BaseException и
            # раньше проходили мимо, оставляя state и фазу навсегда в "running"
            # (мёртвый ран выглядел живым в /workflows). Финализируем и пробрасываем.
            for ph in self.state.phases:
                if ph.status == "running":
                    ph.status = "failed"
                    ph.finished_at = utc_now()
                for ag in ph.agents:
                    if ag.status == "running":
                        ag.status = "failed"
                        ag.finished_at = utc_now()
            self.state.status = "failed"
            self.state.finished_at = utc_now()
            self.state.error = f"{type(e).__name__}: {e}"
            self.save_state()
            raise
        finally:
            if self._tracker:
                self._tracker.stop()
                self._tracker = None

    def _predeclare_inline_phases(self, args: dict[str, Any]) -> None:
        phases = args.get("phases")
        if not isinstance(phases, list) or not phases:
            return
        for i, raw in enumerate(phases, start=1):
            if isinstance(raw, str):
                title = f"Phase {i}"
                detail = ""
            elif isinstance(raw, dict):
                title = str(raw.get("title") or raw.get("name") or f"Phase {i}")
                detail = str(raw.get("detail") or "")
            else:
                continue
            self.state.phases.append(WorkflowPhaseState(
                id=f"phase-{len(self.state.phases) + 1}",
                title=title,
                detail=detail,
                status="pending",
            ))
        self.save_state()

    def _meta_from_args(self, args: dict[str, Any]) -> dict[str, Any]:
        meta = args.get("meta") if isinstance(args.get("meta"), dict) else {}
        name = args.get("name") or meta.get("name") or args.get("goal") or "workflow"
        return {
            **meta,
            "name": str(name),
            "description": str(args.get("description") or meta.get("description") or ""),
        }

    async def _run_python_workflow(self, ctx: WorkflowContext, args: dict[str, Any]) -> Any:
        script = args.get("script")
        path = args.get("path")
        name = args.get("name")
        if not script:
            script_path = self._resolve_workflow_path(path or name)
            with open(script_path, encoding="utf-8") as fh:
                script = fh.read()
            save_json(os.path.join(self.run_dir, "source.json"), {"path": script_path})
        else:
            with open(os.path.join(self.run_dir, "workflow.py"), "w", encoding="utf-8") as fh:
                fh.write(str(script))

        ns: dict[str, Any] = {
            "__builtins__": _safe_builtins(),
            "args": args.get("args") if isinstance(args.get("args"), dict) else {},
        }
        exec(str(script), ns)
        meta = ns.get("meta")
        if isinstance(meta, dict):
            self.state.name = str(meta.get("name") or self.state.name)
            self.state.description = str(meta.get("description") or self.state.description)
            self.save_state()
        run_fn = ns.get("run")
        if not callable(run_fn):
            raise ValueError("Python workflow must define async def run(ctx)")
        result = run_fn(ctx)
        if inspect.isawaitable(result):
            return await result
        return result

    def _resolve_workflow_path(self, value: str) -> str:
        if not value:
            raise ValueError("workflow path or name is required")
        candidates = []
        if os.path.isabs(value):
            candidates.append(value)
        else:
            template_dir = os.path.join(os.path.dirname(__file__), "templates")
            candidates.extend([
                os.path.join(self.working_dir, value),
                os.path.join(self.working_dir, ".data", "workflows", value),
                os.path.join(self.working_dir, ".data", "workflows", value + ".py"),
                os.path.join(template_dir, value),
                os.path.join(template_dir, value + ".py"),
            ])
        for path in candidates:
            if os.path.isfile(path):
                return path
        raise FileNotFoundError(f"workflow not found: {value}")

    async def _run_inline_phases(self, ctx: WorkflowContext, args: dict[str, Any]) -> Any:
        phases = args.get("phases")
        if not isinstance(phases, list) or not phases:
            raise ValueError("workflow requires Python script/path/name or non-empty phases[]")
        output = []
        for i, raw in enumerate(phases, start=1):
            if isinstance(raw, str):
                raw = {"title": f"Phase {i}", "tasks": [{"prompt": raw}]}
            if not isinstance(raw, dict):
                raise ValueError(f"phase {i} must be an object or string")
            title = str(raw.get("title") or raw.get("name") or f"Phase {i}")
            ctx.phase(title, str(raw.get("detail") or ""))
            raw_tasks = raw.get("agents") or raw.get("tasks") or []
            if not isinstance(raw_tasks, list):
                raise ValueError(f"phase {i} tasks/agents must be a list")
            calls = []
            for item in raw_tasks:
                if isinstance(item, str):
                    item = {"prompt": item}
                if not isinstance(item, dict):
                    raise ValueError(f"phase {i} task must be an object or string")
                prompt = str(item.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError(f"phase {i} task prompt is required")
                opts = {k: v for k, v in item.items() if k != "prompt"}
                calls.append(lambda prompt=prompt, opts=opts: ctx.agent(prompt, opts))
            output.append({"phase": title, "results": await ctx.parallel(calls)})
        return output

    async def run_agent_calls(
        self,
        phase: WorkflowPhaseState,
        calls: list[WorkflowAgentCall],
    ) -> list[dict[str, Any] | None]:
        from agent.subagent_api import resolve_subagent_model
        from apis.agent_adapter import get_api_session
        from config.constants import MAX_WORKFLOW_AGENTS_PER_PHASE

        # Лимит агентов на одну фазу (накопительно по всем parallel()/pipeline()
        # этой фазы). Превышение — явная ошибка, чтобы автор скрипта дробил фазу.
        projected = len(phase.agents) + len(calls)
        if projected > MAX_WORKFLOW_AGENTS_PER_PHASE:
            raise ValueError(
                f"phase '{phase.title}' would exceed the limit of "
                f"{MAX_WORKFLOW_AGENTS_PER_PHASE} agents "
                f"(already {len(phase.agents)}, requested {len(calls)} more). "
                f"Split the work across additional phases."
            )

        tasks: list[SubagentTask] = []
        task_states: list[WorkflowAgentState] = []
        out: list[dict[str, Any] | None] = [None] * len(calls)
        for pos, call in enumerate(calls):
            prompt = call.prompt.strip()
            if not prompt:
                raise ValueError("workflow agent prompt is required")
            label = str(call.opts.get("label") or short_agent_label(prompt))
            cache_key = self._agent_cache_key(phase.title, prompt, call.opts)
            # id выделяется синхронно (без await до append ниже), поэтому
            # конкурентные pipeline/parallel-вызовы не коллизятся. Страховка на
            # случай будущей вставки await в эту секцию: гарантируем уникальность.
            agent_id = f"agent-{len(phase.agents) + 1}"
            if any(a.id == agent_id for a in phase.agents):
                agent_id = f"agent-{len(phase.agents) + 1}-{pos}"
            artifact_dir = os.path.join(self.run_dir, "agents", agent_id)
            state = WorkflowAgentState(
                id=agent_id,
                label=label,
                phase=phase.title,
                status="running",
                prompt=prompt,
                model=str(call.opts.get("model") or ""),
                role=str(call.opts.get("role") or call.opts.get("agentType") or ""),
                preset=str(call.opts.get("preset") or ""),
                cache_key=cache_key,
                artifact_dir=artifact_dir,
                started_at=utc_now(),
            )
            phase.agents.append(state)
            self.save_state()
            if self._tracker:
                self._tracker.select_phase(phase)
            cached = self._cache_by_key.get(cache_key) if self.cache else None
            if cached is not None:
                state.status = "done"
                state.cached = True
                state.finished_at = utc_now()
                state.result = dict(cached)
                verdict = _extract_verdict(state.result)
                if verdict:
                    state.result["verdict"] = verdict
                self._write_agent_artifacts(state)
                out[pos] = state.result
                continue
            task_states.append(state)
            tasks.append(SubagentTask(
                prompt=prompt,
                mode="agent",
                model=call.opts.get("model"),
                role=call.opts.get("role") or call.opts.get("agentType"),
                preset=call.opts.get("preset"),
                phase=phase.title,
                label=label,
            ))
        self.save_state()

        if not tasks:
            return out

        api_sess = get_api_session()
        default_pid = api_sess.provider_id if api_sess else ""
        default_mid = api_sess.model_id if api_sess else self.model
        task_models = []
        for task in tasks:
            try:
                _, mid = resolve_subagent_model(task.model, default_pid, default_mid)
            except Exception:
                mid = default_mid
            task_models.append(mid or "")

        buffers = []
        for i, (task, state) in enumerate(zip(tasks, task_states)):
            buf = SubagentBuffer(
                index=i,
                mode=task.mode,
                prompt=task.prompt,
                model_label=task_models[i],
                role=task.role or "",
                preset=task.preset or "",
                depends_on=[],
                phase=task.phase or "",
                label=task.label or "",
            )
            buffers.append(buf)
            self._buffers_by_agent_id[state.id] = buf
        if self._tracker:
            self._tracker.select_phase(phase)

        orchestrator = SubagentOrchestrator(
            model=self.model,
            working_dir=self.working_dir,
            buffers=buffers,
            isolate=self.isolate,
        )

        results = await orchestrator.run(tasks)

        result_iter = iter(results)
        for idx, value in enumerate(out):
            if value is not None:
                continue
            state = task_states.pop(0)
            result = next(result_iter)
            state.status = "failed" if result.error else "done"
            state.finished_at = utc_now()
            state.result = _result_to_dict(result)
            verdict = _extract_verdict(state.result)
            if verdict:
                state.result["verdict"] = verdict
            self._write_agent_artifacts(state)
            out[idx] = state.result
            if result.error and self.fail_fast:
                self.save_state()
                raise RuntimeError(f"workflow agent {state.label} failed: {result.error}")
        self.save_state()
        return out

    def _select_phase(self, phase: WorkflowPhaseState) -> None:
        if self._tracker:
            self._tracker.select_phase(phase)

    def _agent_cache_key(self, phase: str, prompt: str, opts: dict[str, Any]) -> str:
        payload = {
            "phase": phase,
            "prompt": prompt,
            "opts": opts,
            "model": self.model,
            "isolate": self.isolate,
            "base": _git_head(self.working_dir),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _write_agent_artifacts(self, state: WorkflowAgentState) -> None:
        os.makedirs(state.artifact_dir, exist_ok=True)
        with open(os.path.join(state.artifact_dir, "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write(state.prompt)
        save_json(os.path.join(state.artifact_dir, "result.json"), state.result)
        with open(os.path.join(state.artifact_dir, "result.md"), "w", encoding="utf-8") as fh:
            if state.cached:
                fh.write("[cached]\n\n")
            response = state.result.get("response") if isinstance(state.result, dict) else ""
            error = state.result.get("error") if isinstance(state.result, dict) else ""
            fh.write(str(response or error or ""))

    def _format_output(self) -> str:
        state = self.state
        lines = [
            f"Workflow {state.name} completed",
            f"run_id: {state.id}",
            f"state: {os.path.join(self.run_dir, 'state.json')}",
            "",
        ]
        for phase in state.phases:
            done = sum(1 for a in phase.agents if a.status in ("done", "failed"))
            failed = sum(1 for a in phase.agents if a.status == "failed")
            suffix = f"{done}/{len(phase.agents)} agents"
            if failed:
                suffix += f", {failed} failed"
            lines.append(f"## {phase.title} — {phase.status} ({suffix})")
            for log in phase.logs:
                lines.append(f"- log: {log}")
            for agent in phase.agents:
                err = agent.result.get("error") if isinstance(agent.result, dict) else ""
                verdict = agent.result.get("verdict") if isinstance(agent.result, dict) else ""
                mark = "ERROR" if err else (f"VERDICT:{verdict}" if verdict else "OK")
                cached = " cached" if agent.cached else ""
                artifact = f" ({agent.artifact_dir})" if agent.artifact_dir else ""
                lines.append(f"- {agent.label}: {mark}{cached}{artifact}")
            lines.append("")
        lines.append("Result:")
        lines.append(str(state.result))
        return "\n".join(lines)


def _extract_verdict(value: Any) -> str:
    text = ""
    if isinstance(value, dict):
        text = str(value.get("response") or value.get("output") or value.get("result") or "")
    else:
        text = str(value or "")
    upper = text.upper()
    for verdict in ("PASS", "FAIL", "PARTIAL"):
        if f"VERDICT: {verdict}" in upper or f"VERDICT:{verdict}" in upper:
            return verdict
    return ""


def _extract_verdict(value: Any) -> str:
    text = ""
    if isinstance(value, dict):
        text = str(value.get("response") or value.get("output") or value.get("result") or "")
    else:
        text = str(value or "")
    upper = text.upper()
    for verdict in ("PASS", "FAIL", "PARTIAL"):
        if f"VERDICT: {verdict}" in upper or f"VERDICT:{verdict}" in upper:
            return verdict
    return ""

def _git_head(working_dir: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _safe_builtins() -> dict[str, Any]:
    # NOTE: keep this allowlist to pure data/computation modules only.
    # pathlib/os/subprocess (and anything that transitively exposes them) must
    # never be importable from a workflow script, since scripts may be untrusted.
    allowed_modules = {"json", "math", "re", "datetime"}

    def _limited_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = str(name).split(".", 1)[0]
        if root not in allowed_modules:
            raise ImportError(f"workflow import blocked: {name}")
        return __import__(name, globals, locals, fromlist, level)

    return {
        "__import__": _limited_import,
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "callable": callable,
        "dict": dict,
        "enumerate": enumerate,
        "Exception": Exception,
        "float": float,
        "hasattr": hasattr,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "repr": repr,
        "round": round,
        "set": set,
        "sorted": sorted,
        "RuntimeError": RuntimeError,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "TypeError": TypeError,
        "ValueError": ValueError,
        "zip": zip,
    }

def _result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "task_index": result.task_index,
        "mode": result.mode,
        "response": result.response,
        "iterations": result.iterations,
        "elapsed": result.elapsed,
        "error": result.error,
        "model_label": result.model_label,
        "phase": result.phase,
        "label": result.label,
        "branch": result.branch,
        "worktree_path": result.worktree_path,
        "commit_sha": result.commit_sha,
        "commits_count": result.commits_count,
        "files_changed": list(result.files_changed or []),
        "diff_stat": result.diff_stat,
        "has_changes": result.has_changes,
    }