"""tools/subagent.py — _parse_depends_on + execute_subagent (runner mocked)."""

import sys
import types
from dataclasses import dataclass, field
from typing import Optional

import pytest

from tools.subagent import _parse_depends_on, execute_subagent, set_subagent_context
from tools.models import ToolCall

def _call(args: dict) -> ToolCall:
    return ToolCall(command="subagent", tool_name="subagent", args=args)


class TestSubagentContextDiscipline:
    """The subagent mode_block must tell the agent to locate-then-read-narrow.

    Subagent context is small and unpruned, so reading whole files is the main
    token sink. We assert the guidance text is present in the source (the inline
    block is built per-instance, so we check the module source directly).
    """

    def test_mode_block_teaches_locate_then_narrow(self):
        import inspect
        import agent.subagent_api as sa

        src = inspect.getsource(sa._ApiSubagentRunner._build_system_prompt)
        assert "LOCATE, then read NARROW" in src
        assert "FIRST tool is LSP" in src
        # context is flagged as small/lean (no longer the false "NOT auto-pruned":
        # the runner DOES prune now, so the prompt must not claim otherwise)
        assert "keep it lean" in src
        assert "NOT auto-pruned" not in src

    def test_mode_block_forbids_sleep_waiting_for_peer(self):
        # Regression: subagent did `sleep 40; if [ -f peer-file ]` to wait for a
        # sibling that didn't exist (it was alone in its wave). The block must ban
        # poll/sleep-for-peer and reference depends_on as the real mechanism.
        import inspect
        import agent.subagent_api as sa

        src = inspect.getsource(sa._ApiSubagentRunner._build_system_prompt)
        assert "NEVER WAIT, NEVER SLEEP FOR A PEER" in src
        assert "ALWAYS A BUG" in src
        assert "depends_on" in src
        # peer-reference files are explicitly read-once, never poll
        assert "never poll" in src
        # solo-wave branch exists
        assert "ONLY subagent in this wave" in src

    def test_runner_accepts_wave_size(self):
        # The runner must take wave_size so it can tell the agent it's alone.
        import inspect
        import agent.subagent_api as sa

        sig = inspect.signature(sa._ApiSubagentRunner.__init__)
        assert "wave_size" in sig.parameters

    def test_token_budget_guard_uses_runner_counter_not_buffer(self):
        # ROOT CAUSE: the runaway backstop read self.buffer.total_tokens, but the
        # subagent tool builds runners with buffer=None → spent always 0 → the
        # 350k guard NEVER fired. Now it must read a runner-owned counter that is
        # fed by _track_usage independent of buffer.
        import inspect
        import agent.subagent_api as sa

        run_src = inspect.getsource(sa._ApiSubagentRunner.run)
        assert "self._spent_tokens" in run_src
        assert "self.buffer.total_tokens" not in run_src

        track_src = inspect.getsource(sa._ApiSubagentRunner._track_usage)
        assert "_spent_tokens" in track_src
        # accumulates even when buffer is None (no `if self.buffer` gate on the count)
        assert "input_tokens" in track_src or "total_tokens" in track_src

    def test_track_usage_accumulates_without_buffer(self):
        # Build a bare runner-like object exercising only _track_usage arithmetic.
        import agent.subagent_api as sa

        class _Bare:
            buffer = None
            _spent_tokens = 0
            _track_usage = sa._ApiSubagentRunner._track_usage

        b = _Bare()
        b._track_usage({"input_tokens": 100, "output_tokens": 50})
        b._track_usage({"total_tokens": 200})
        assert b._spent_tokens == 350
        # malformed usage doesn't crash or corrupt the counter
        b._track_usage(None)
        b._track_usage("garbage")
        assert b._spent_tokens == 350

    def test_budget_and_iteration_stops_report_error(self):
        # Исчерпание token-budget или лимита итераций = работа НЕ доведена.
        # Раньше оба возвращали error=None → workflow/главный агент считали это
        # полным успехом (видно в super_shop: фаза done при незавершённой работе).
        # Теперь оба возвращают error-строку (текст работы сохранён в final).
        import inspect
        import agent.subagent_api as sa

        run_src = inspect.getsource(sa._ApiSubagentRunner.run)
        # обе ветки стопа должны возвращать ненулевой error
        assert "token budget reached (work likely incomplete)" in run_src
        assert "iteration limit reached (work likely incomplete)" in run_src
        # нормальное завершение (нет tool calls) остаётся успехом — ровно ОДИН
        # такой return с error=None допустим, не больше (стопы его не используют)
        assert run_src.count("return final, iterations + 1, None") == 1

    def test_model_call_has_timeout(self):
        # A hung onlysq stream must not block a subagent (and the whole pool)
        # forever. The run loop must wrap _call_model in asyncio.wait_for and
        # recover from a timeout instead of propagating/hanging.
        import inspect
        import agent.subagent_api as sa

        assert hasattr(sa, "MODEL_CALL_TIMEOUT_SEC")
        run_src = inspect.getsource(sa._ApiSubagentRunner.run)
        assert "asyncio.wait_for(" in run_src
        assert "MODEL_CALL_TIMEOUT_SEC" in run_src
        assert "TimeoutError" in run_src

    def test_subagent_prunes_context_before_model_call(self):
        # ROOT CAUSE of "subagent hits 350k tokens while main loop stays ~70k":
        # the main loop calls prune_messages before every model call, the subagent
        # used to send raw self.session.messages and grow linearly. All 3 model-call
        # branches must send the pruned copy, and a _pruned_messages helper must exist.
        import inspect
        import agent.subagent_api as sa

        assert hasattr(sa._ApiSubagentRunner, "_pruned_messages")
        helper = inspect.getsource(sa._ApiSubagentRunner._pruned_messages)
        assert "prune_messages" in helper

        call = inspect.getsource(sa._ApiSubagentRunner._call_model)
        assert "self._pruned_messages()" in call
        assert "llm.ainvoke(msgs)" in call
        assert "llm.astream(msgs)" in call
        # no branch may send the raw, unpruned message list to the model
        assert "ainvoke(self.session.messages)" not in call
        assert "astream(self.session.messages)" not in call


class TestParseDependsOn:
    def test_none(self):
        assert _parse_depends_on(None) == []

    def test_int(self):
        assert _parse_depends_on(3) == [3]

    def test_list_of_ints(self):
        assert _parse_depends_on([1, 2, 3]) == [1, 2, 3]

    def test_list_of_str_ints(self):
        assert _parse_depends_on(["1", "2"]) == [1, 2]

    def test_str_comma_separated(self):
        assert _parse_depends_on("1,2,3") == [1, 2, 3]

    def test_str_space_separated(self):
        assert _parse_depends_on("1 2 3") == [1, 2, 3]

    def test_str_mixed_separators(self):
        assert _parse_depends_on("1, 2  3") == [1, 2, 3]

    def test_invalid_entries_skipped(self):
        assert _parse_depends_on([1, "x", 2, None]) == [1, 2]

    def test_unsupported_type(self):
        assert _parse_depends_on({"a": 1}) == []

    def test_tuple(self):
        assert _parse_depends_on((4, 5)) == [4, 5]

    def test_empty_str(self):
        assert _parse_depends_on("") == []

# --- Fakes for agent.subagent (so execute_subagent never spawns real work) ---

@dataclass
class _FakeTask:
    prompt: str
    mode: str = "agent"
    model: Optional[str] = None
    role: Optional[str] = None
    preset: Optional[str] = None
    depends_on: list = field(default_factory=list)

@dataclass
class _FakeResult:
    error: Optional[str] = None

class _FakeOrchestrator:
    last_init = None
    captured_tasks = None

    def __init__(self, model=None, working_dir=None, on_status=None, isolate=False):
        self.model = model
        self.working_dir = working_dir
        self.on_status = on_status
        self.isolate = isolate
        self.run_dir = "/tmp/fake-run"
        _FakeOrchestrator.last_init = self

    async def run(self, tasks):
        _FakeOrchestrator.captured_tasks = tasks
        # one ok result per task by default
        return [_FakeResult(error=None) for _ in tasks]

def _install_fake_agent_subagent(monkeypatch, results=None, orchestrator_cls=None):
    """Inject a fake agent.subagent module so the lazy import inside
    execute_subagent picks it up without running anything real."""
    orch = orchestrator_cls or _FakeOrchestrator

    def _format(results_, run_dir=None):
        return f"FORMATTED({len(results_)} results, run_dir={run_dir})"

    mod = types.ModuleType("agent.subagent")
    mod.SubagentTask = _FakeTask
    mod.SubagentResult = _FakeResult
    mod.SubagentOrchestrator = orch
    mod.format_subagent_results = _format
    monkeypatch.setitem(sys.modules, "agent.subagent", mod)
    return mod

@pytest.fixture(autouse=True)
def _reset_orch():
    _FakeOrchestrator.last_init = None
    _FakeOrchestrator.captured_tasks = None
    yield

class TestExecuteSubagentValidation:
    def test_missing_tasks(self):
        r = execute_subagent(_call({}))
        assert r.status == "error"
        assert "tasks" in r.output
        assert r.exit_code == 1

    def test_empty_tasks_list(self):
        r = execute_subagent(_call({"tasks": []}))
        assert r.status == "error"
        assert "tasks" in r.output

    def test_all_tasks_empty_prompt(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        r = execute_subagent(_call({"tasks": [{"prompt": "   "}, {"prompt": ""}]}))
        assert r.status == "error"
        assert "prompt" in r.output

class TestExecuteSubagentParsing:
    def test_basic_single_task(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        r = execute_subagent(_call({"tasks": [{"prompt": "do x"}]}))
        assert r.status == "ok"
        assert r.exit_code == 0
        assert "FORMATTED(1 results" in r.output
        tasks = _FakeOrchestrator.captured_tasks
        assert len(tasks) == 1
        assert tasks[0].prompt == "do x"
        assert tasks[0].mode == "agent"
        assert tasks[0].model is None

    def test_prompt_stripped(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "  hello  "}]}))
        assert _FakeOrchestrator.captured_tasks[0].prompt == "hello"

    def test_mode_is_always_agent(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "mode": "PLAN"}]}))
        assert _FakeOrchestrator.captured_tasks[0].mode == "agent"

    def test_model_override_stripped(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "model": "  gpt-5.2 "}]}))
        assert _FakeOrchestrator.captured_tasks[0].model == "gpt-5.2"

    def test_model_blank_becomes_none(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "model": "   "}]}))
        assert _FakeOrchestrator.captured_tasks[0].model is None

    def test_model_non_string_becomes_none(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "model": 123}]}))
        assert _FakeOrchestrator.captured_tasks[0].model is None

    def test_role_normalized(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "role": " Coder "}]}))
        assert _FakeOrchestrator.captured_tasks[0].role == "coder"

    def test_role_blank_none(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "role": "  "}]}))
        assert _FakeOrchestrator.captured_tasks[0].role is None

    def test_preset_stripped(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "preset": " test-writer "}]}))
        assert _FakeOrchestrator.captured_tasks[0].preset == "test-writer"

    def test_preset_blank_none(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [{"prompt": "p", "preset": ""}]}))
        assert _FakeOrchestrator.captured_tasks[0].preset is None

    def test_depends_on_parsed(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [
            {"prompt": "a"},
            {"prompt": "b", "depends_on": "1"},
        ]}))
        tasks = _FakeOrchestrator.captured_tasks
        assert tasks[1].depends_on == [1]

    def test_empty_prompt_task_skipped(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        execute_subagent(_call({"tasks": [
            {"prompt": "keep"},
            {"prompt": "   "},
            {"prompt": "also"},
        ]}))
        prompts = [t.prompt for t in _FakeOrchestrator.captured_tasks]
        assert prompts == ["keep", "also"]

    def test_more_than_100_tasks_truncated(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        tasks_raw = [{"prompt": f"t{i}"} for i in range(150)]
        execute_subagent(_call({"tasks": tasks_raw}))
        assert len(_FakeOrchestrator.captured_tasks) == 100

    def test_context_passed_to_orchestrator(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        set_subagent_context(model="my-model", working_dir="/work", event_handler=None)
        try:
            execute_subagent(_call({"tasks": [{"prompt": "p"}], "isolate": True}))
        finally:
            set_subagent_context(model="", working_dir="", event_handler=None)
        orch = _FakeOrchestrator.last_init
        assert orch.model == "my-model"
        assert orch.working_dir == "/work"
        assert orch.isolate is True

class TestExecuteSubagentResults:
    def test_error_result_marks_error_status(self, monkeypatch):
        class _ErrOrch(_FakeOrchestrator):
            async def run(self, tasks):
                _FakeOrchestrator.captured_tasks = tasks
                return [_FakeResult(error="boom")]

        _install_fake_agent_subagent(monkeypatch, orchestrator_cls=_ErrOrch)
        r = execute_subagent(_call({"tasks": [{"prompt": "p"}]}))
        assert r.status == "error"
        assert r.exit_code == 1

    def test_mixed_results_error_status(self, monkeypatch):
        class _MixOrch(_FakeOrchestrator):
            async def run(self, tasks):
                _FakeOrchestrator.captured_tasks = tasks
                return [_FakeResult(error=None), _FakeResult(error="x")]

        _install_fake_agent_subagent(monkeypatch, orchestrator_cls=_MixOrch)
        r = execute_subagent(_call({"tasks": [{"prompt": "a"}, {"prompt": "b"}]}))
        assert r.status == "error"

    def test_status_callback_invoked_via_handler(self, monkeypatch):
        _install_fake_agent_subagent(monkeypatch)
        captured = []

        class _Handler:
            def on_status(self, msg, level="info"):
                captured.append((msg, level))

        set_subagent_context(model="m", working_dir="/w", event_handler=_Handler())
        try:
            execute_subagent(_call({"tasks": [{"prompt": "p"}]}))
            orch = _FakeOrchestrator.last_init
            orch.on_status(0, "started")
        finally:
            set_subagent_context(model="", working_dir="", event_handler=None)
        assert captured
        assert "Subagent 1" in captured[0][0]
        assert "started" in captured[0][0]