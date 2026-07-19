"""agent/context.py — AgentContext."""

import os

from agent.context import AgentContext


class TestDefaults:
    def test_default_values(self):
        ctx = AgentContext()
        assert ctx.plan is None
        assert ctx.working_dir == os.getcwd()
        assert ctx.plan_dir == ""
        assert ctx.event_handler is None
        assert ctx.original_message == ""
        assert ctx.interrupted is False
        assert ctx.hard_interrupted is False
        assert ctx.mode == "agent"
        assert ctx.session_id == ""
        assert ctx.last_fs_snapshot is None

    def test_step_tracker_initialized(self):
        ctx = AgentContext()
        assert ctx.step_tracker is not None
        assert ctx.step_tracker.files_changed == set()

    def test_step_trackers_not_shared(self):
        c1 = AgentContext()
        c2 = AgentContext()
        c1.step_tracker.files_changed.add("a.py")
        assert "a.py" not in c2.step_tracker.files_changed


class TestEffectivePlanDir:
    def test_falls_back_to_working_dir(self):
        ctx = AgentContext(working_dir="/tmp/work")
        assert ctx.effective_plan_dir == "/tmp/work"

    def test_uses_explicit_plan_dir(self):
        ctx = AgentContext(working_dir="/tmp/work", plan_dir="/tmp/plans")
        assert ctx.effective_plan_dir == "/tmp/plans"


class TestResetInterrupt:
    def test_clears_both_flags(self):
        ctx = AgentContext()
        ctx.interrupted = True
        ctx.hard_interrupted = True
        ctx.reset_interrupt()
        assert ctx.interrupted is False
        assert ctx.hard_interrupted is False


