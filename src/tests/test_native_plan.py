from planner import (
    StepStatus,
    apply_plan_commands,
    parse_native_plan_commands,
    render_plan_panel,
)


def test_native_plan_is_structured_and_renderable():
    commands = parse_native_plan_commands([
        {
            "name": "plan",
            "args": {
                "action": "create",
                "goal": "Проверить план",
                "steps": [
                    {"title": "Первый", "status": "done"},
                    {"title": "Второй", "status": "in_progress"},
                    {"title": "Третий"},
                ],
            },
        },
        {"name": "shell", "args": {"command": "true"}},
    ])

    plan = apply_plan_commands(None, commands)
    assert plan.goal == "Проверить план"
    assert [step.status for step in plan.steps] == [
        StepStatus.DONE,
        StepStatus.IN_PROGRESS,
        StepStatus.PENDING,
    ]
    assert "Plan [1/3]" in plan.render_for_context()
    assert render_plan_panel(plan) is not None


def test_invalid_native_plan_is_ignored():
    assert parse_native_plan_commands([
        {"name": "plan", "args": {"action": "create", "steps": []}},
    ]) == []
