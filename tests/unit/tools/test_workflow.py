"""tools/workflow.py — статус результата отражает упавших агентов."""

import sys
import types
from types import SimpleNamespace

from tools.models import ToolCall
from tools.workflow import _collect_failed_agents, execute_workflow


def _agent(label, status, error=""):
    return SimpleNamespace(label=label, status=status, result={"error": error} if error else {})


def _runner(*phases):
    state = SimpleNamespace(status="completed", phases=list(phases))
    return SimpleNamespace(state=state)


class TestCollectFailedAgents:
    """Регрессия: workflow возвращал exit_code=0, даже когда все агенты падали
    (fail_fast=False лишь записывал их в state). Главный агент видел зелёный
    результат на провальном ране."""

    def test_no_failures_returns_empty(self):
        ph = SimpleNamespace(title="Build", agents=[_agent("a", "done"), _agent("b", "done")])
        assert _collect_failed_agents(_runner(ph)) == []

    def test_collects_failed_with_label_phase_error(self):
        ph = SimpleNamespace(title="Build", agents=[
            _agent("a", "done"),
            _agent("b", "failed", "boom"),
        ])
        failed = _collect_failed_agents(_runner(ph))
        assert failed == [("b", "Build", "boom")]

    def test_failed_without_error_detail(self):
        ph = SimpleNamespace(title="Verify", agents=[_agent("v", "failed")])
        failed = _collect_failed_agents(_runner(ph))
        assert failed == [("v", "Verify", "(no detail)")]

    def test_multiple_phases(self):
        p1 = SimpleNamespace(title="P1", agents=[_agent("x", "failed", "e1")])
        p2 = SimpleNamespace(title="P2", agents=[_agent("y", "done"), _agent("z", "failed", "e2")])
        failed = _collect_failed_agents(_runner(p1, p2))
        assert ("x", "P1", "e1") in failed
        assert ("z", "P2", "e2") in failed
        assert len(failed) == 2

    def test_no_state_safe(self):
        assert _collect_failed_agents(SimpleNamespace(state=None)) == []


class _FakeRunner:
    """Раннер, чей run() возвращает текст, а state несёт заданных агентов."""

    def __init__(self, *, run_status, agents, **_kw):
        self._run_status = run_status
        ph = SimpleNamespace(title="Verify", agents=agents)
        self.state = SimpleNamespace(status=run_status, phases=[ph])

    async def run(self, args):
        return "workflow output text"


def _install_fakes(monkeypatch, *, run_status, agents):
    """Подменяет get_subagent_context + WorkflowRunner для execute_workflow."""
    sub_mod = types.ModuleType("tools.subagent")
    sub_mod.get_subagent_context = lambda: ("model", "/tmp", None)
    monkeypatch.setitem(sys.modules, "tools.subagent", sub_mod)

    runner_mod = types.ModuleType("workflows.runner")
    runner_mod.WorkflowRunner = lambda **kw: _FakeRunner(
        run_status=run_status, agents=agents, **kw
    )
    monkeypatch.setitem(sys.modules, "workflows.runner", runner_mod)


class TestExecuteWorkflowStatus:
    """A2: execute_workflow должен возвращать error при упавших агентах, а не
    молчаливый exit_code=0 (главный агент иначе считает провал успехом)."""

    def test_all_done_returns_ok(self, monkeypatch):
        _install_fakes(monkeypatch, run_status="completed",
                       agents=[_agent("a", "done"), _agent("b", "done")])
        res = execute_workflow(ToolCall(command="workflow", tool_name="workflow", args={}))
        assert res.status == "ok"
        assert res.exit_code == 0

    def test_failed_agent_returns_error(self, monkeypatch):
        _install_fakes(monkeypatch, run_status="completed",
                       agents=[_agent("a", "done"), _agent("verify", "failed", "tests failed")])
        res = execute_workflow(ToolCall(command="workflow", tool_name="workflow", args={}))
        assert res.status == "error"
        assert res.exit_code == 1
        assert "verify" in res.output
        assert "tests failed" in res.output

    def test_run_status_failed_returns_error(self, monkeypatch):
        # Даже если по агентам пусто, но сам run помечен failed — это error.
        _install_fakes(monkeypatch, run_status="failed", agents=[])
        res = execute_workflow(ToolCall(command="workflow", tool_name="workflow", args={}))
        assert res.status == "error"
        assert res.exit_code == 1
