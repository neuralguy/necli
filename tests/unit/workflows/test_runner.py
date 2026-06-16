"""workflows/runner.py — финализация state при прерывании (BaseException)."""

import asyncio

import pytest

from workflows.runner import WorkflowRunner
from workflows.specs import WorkflowAgentState, WorkflowPhaseState


def _runner(tmp_path):
    r = WorkflowRunner(model="m", working_dir=str(tmp_path), isolate=False, cache=False)
    r._init_state({"name": "t"})
    return r


class TestRunFinalizesOnInterrupt:
    """Регрессия: run() ловил только Exception. KeyboardInterrupt и
    asyncio.CancelledError — это BaseException, они проходили мимо, оставляя
    state и агентов навсегда в 'running' (мёртвый ран выглядел живым в /workflows)."""

    @pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
    def test_state_failed_and_running_agents_finalized(self, tmp_path, exc, monkeypatch):
        r = _runner(tmp_path)
        captured = {}

        # run() пересоздаёт state в _init_state, поэтому running-фазу с зависшим
        # агентом добавляем ВНУТРИ выполнения (как реальный прерванный ран), затем
        # бросаем BaseException.
        async def boom(ctx, args):
            phase = WorkflowPhaseState(id="p1", title="P", status="running")
            phase.agents.append(
                WorkflowAgentState(id="a1", label="x", phase="P", status="running")
            )
            r.state.phases.append(phase)
            captured["phase"] = phase
            raise exc()

        monkeypatch.setattr(r, "_run_inline_phases", boom)

        with pytest.raises(exc):
            asyncio.run(r.run({}))

        phase = captured["phase"]
        # run помечен failed, зависший агент и фаза тоже finalized
        assert r.state.status == "failed"
        assert r.state.finished_at
        assert phase.status == "failed"
        assert phase.agents[0].status == "failed"
        assert phase.agents[0].finished_at

    def test_normal_exception_still_failed(self, tmp_path, monkeypatch):
        r = _runner(tmp_path)

        async def boom(ctx, args):
            raise ValueError("nope")

        monkeypatch.setattr(r, "_run_inline_phases", boom)
        with pytest.raises(ValueError):
            asyncio.run(r.run({}))
        assert r.state.status == "failed"
        assert "ValueError" in r.state.error
