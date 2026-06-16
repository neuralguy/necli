"""planner.py — Plan, PlanStep, parse/apply commands."""

from planner import (
    Plan, PlanStep, StepStatus,
    parse_plan_commands, strip_plan_commands,
    apply_plan_commands,
    _render_plan_markdown, _parse_plan_markdown,
    save_plan_file, load_plan_file, delete_plan_file,
)


class TestPlanStep:
    def test_default_status(self):
        s = PlanStep(title="x")
        assert s.status == StepStatus.PENDING
        assert s.notes == ""

    def test_to_dict_with_notes(self):
        s = PlanStep(title="x", status=StepStatus.DONE, notes="ok")
        d = s._to_dict()
        assert d == {"title": "x", "status": "done", "notes": "ok"}

    def test_to_dict_skips_empty_notes(self):
        s = PlanStep(title="x")
        d = s._to_dict()
        assert "notes" not in d

    def test_from_dict_with_invalid_status(self):
        s = PlanStep._from_dict({"title": "x", "status": "weird"})
        assert s.status == StepStatus.PENDING


class TestPlanBasic:
    def test_empty(self):
        p = Plan(goal="x")
        assert p.total == 0
        assert p.done_count == 0
        assert p.is_complete is False
        assert p.progress_str == "0/0"

    def test_set_steps_from_strings(self):
        p = Plan(goal="x")
        p.set_steps(["a", "b", "c"])
        assert p.total == 3
        assert all(s.status == StepStatus.PENDING for s in p.steps)

    def test_set_steps_from_dicts(self):
        p = Plan(goal="x")
        p.set_steps([{"title": "a"}, {"title": "b", "status": "done"}])
        assert p.steps[1].status == StepStatus.DONE


class TestUpdateStep:
    def test_change_status(self):
        p = Plan(goal="x")
        p.set_steps(["a", "b"])
        p.update_step(0, status="done")
        assert p.steps[0].status == StepStatus.DONE

    def test_add_notes(self):
        p = Plan(goal="x")
        p.set_steps(["a"])
        p.update_step(0, notes="explanation")
        assert p.steps[0].notes == "explanation"

    def test_out_of_range_ignored(self):
        p = Plan(goal="x")
        p.set_steps(["a"])
        p.update_step(99, status="done")  # no-op
        assert p.steps[0].status == StepStatus.PENDING


class TestProgress:
    def test_done_count(self):
        p = Plan(goal="x")
        p.set_steps(["a", "b", "c"])
        p.update_step(0, status="done")
        p.update_step(1, status="skipped")
        assert p.done_count == 2

    def test_is_complete(self):
        p = Plan(goal="x")
        p.set_steps(["a", "b"])
        p.update_step(0, status="done")
        p.update_step(1, status="done")
        assert p.is_complete is True

    def test_current_step(self):
        p = Plan(goal="x")
        p.set_steps(["a", "b", "c"])
        p.update_step(0, status="done")
        p.update_step(1, status="in_progress")
        assert p.current_step.title == "b"

    def test_current_pending_fallback(self):
        p = Plan(goal="x")
        p.set_steps(["a", "b"])
        p.update_step(0, status="done")
        # ни одного in_progress → первый pending
        assert p.current_step.title == "b"


class TestPlanBlockParsing:
    def test_strip_removes_block(self):
        text = 'before\n:::call plan\n{"action":"create","steps":["a","b","c"]}\ncall:::\nafter'
        result = strip_plan_commands(text)
        assert ":::call" not in result
        assert "call:::" not in result
        assert "before" in result
        assert "after" in result

    def test_parse_create(self):
        text = ':::call plan\n{"action": "create", "steps": ["a", "b", "c"]}\ncall:::'
        cmds = parse_plan_commands(text)
        assert len(cmds) == 1
        assert cmds[0].action == "create"

    def test_parse_create_less_than_3_rejected(self):
        text = ':::call plan\n{"action": "create", "steps": ["a", "b"]}\ncall:::'
        cmds = parse_plan_commands(text)
        assert cmds == []

    def test_parse_create_no_steps_rejected(self):
        text = ':::call plan\n{"action": "create"}\ncall:::'
        assert parse_plan_commands(text) == []

    def test_two_colon_plan_parses(self):
        # Модель роняет одно двоеточие — ::call plan тоже должен исполняться.
        text = '::call plan\n{"action": "create", "steps": ["a", "b", "c"]}\ncall::'
        cmds = parse_plan_commands(text)
        assert len(cmds) == 1 and cmds[0].action == "create"

    def test_two_colon_plan_stripped(self):
        text = 'before\n::call plan\n{"action":"create","steps":["a","b","c"]}\ncall::\nafter'
        result = strip_plan_commands(text)
        assert "call" not in result.replace("before", "").replace("after", "")
        assert "before" in result and "after" in result

    def test_parse_update(self):
        text = ':::call plan\n{"action": "update", "step": 1, "status": "done"}\ncall:::'
        cmds = parse_plan_commands(text)
        assert len(cmds) == 1
        assert cmds[0].action == "update"

    def test_parse_invalid_action(self):
        text = ':::call plan\n{"action": "explode"}\ncall:::'
        assert parse_plan_commands(text) == []

    def test_parse_invalid_json_repaired(self):
        # JSON с trailing comma / single quotes — должен быть восстановлен
        text = ":::call plan\n{'action': 'create', 'steps': ['a','b','c',]}\ncall:::"
        cmds = parse_plan_commands(text)
        assert len(cmds) == 1


class TestApplyPlanCommands:
    def test_create_new(self):
        from planner import PlanCommand
        cmd = PlanCommand(action="create", data={"goal": "g", "steps": ["a", "b", "c"]})
        plan = apply_plan_commands(None, [cmd])
        assert plan is not None
        assert plan.goal == "g"
        assert plan.total == 3

    def test_update_by_1based_id(self):
        p = Plan(goal="g")
        p.set_steps(["a", "b"])
        from planner import PlanCommand
        cmd = PlanCommand(action="update", data={"step": 1, "status": "done"})
        apply_plan_commands(p, [cmd])
        assert p.steps[0].status == StepStatus.DONE

    def test_update_by_title_substring(self):
        p = Plan(goal="g")
        p.set_steps(["create database", "run tests", "deploy"])
        from planner import PlanCommand
        cmd = PlanCommand(action="update", data={"title": "tests", "status": "done"})
        apply_plan_commands(p, [cmd])
        assert p.steps[1].status == StepStatus.DONE

    def test_add_step(self):
        p = Plan(goal="g")
        p.set_steps(["a", "b"])
        from planner import PlanCommand
        cmd = PlanCommand(action="add_step", data={"title": "c"})
        apply_plan_commands(p, [cmd])
        assert p.total == 3
        assert p.steps[-1].title == "c"

    def test_remove_step(self):
        p = Plan(goal="g")
        p.set_steps(["a", "b", "c"])
        from planner import PlanCommand
        cmd = PlanCommand(action="remove_step", data={"step": 1})
        apply_plan_commands(p, [cmd])
        assert p.total == 2


class TestRenderMarkdownRoundtrip:
    def test_roundtrip(self):
        p = Plan(goal="my goal")
        p.set_steps(["a", "b", "c"])
        p.update_step(0, status="done")
        p.update_step(1, status="in_progress", notes="working")
        md = _render_plan_markdown(p)
        parsed = _parse_plan_markdown(md)
        assert parsed is not None
        assert parsed.goal == "my goal"
        assert parsed.total == 3
        assert parsed.steps[0].status == StepStatus.DONE
        assert parsed.steps[1].status == StepStatus.IN_PROGRESS
        assert parsed.steps[1].notes == "working"

    def test_empty_markdown(self):
        assert _parse_plan_markdown("") is None

    def test_no_goal_returns_none(self):
        assert _parse_plan_markdown("just text\nno goal") is None


class TestFileOps:
    def test_save_and_load(self, tmp_workdir):
        p = Plan(goal="g")
        p.set_steps(["a", "b", "c"])
        save_plan_file(p, str(tmp_workdir))
        assert (tmp_workdir / ".plan.md").exists()
        loaded = load_plan_file(str(tmp_workdir))
        assert loaded is not None
        assert loaded.goal == "g"
        assert loaded.total == 3

    def test_load_missing(self, tmp_workdir):
        assert load_plan_file(str(tmp_workdir)) is None

    def test_delete(self, tmp_workdir):
        p = Plan(goal="g")
        p.set_steps(["a", "b", "c"])
        save_plan_file(p, str(tmp_workdir))
        delete_plan_file(str(tmp_workdir))
        assert not (tmp_workdir / ".plan.md").exists()

    def test_delete_missing_no_error(self, tmp_workdir):
        # не должно падать
        delete_plan_file(str(tmp_workdir))