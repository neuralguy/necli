
import pytest

from tools.poll import execute_poll

@pytest.fixture
def stub_run_poll(monkeypatch):
    """Подменяет ui.poll.run_poll, чтобы не запускать интерактивный UI."""
    calls = {}

    def _set(results, capture=None):
        def fake_run_poll(steps):
            calls["steps"] = steps
            if callable(results):
                return results(steps)
            return results
        monkeypatch.setattr("tools.poll.run_poll", fake_run_poll)
        return calls

    return _set

def test_no_steps_returns_error(make_tool_call):
    call = make_tool_call("poll", args={})
    res = execute_poll(call)
    assert res.status == "error"
    assert res.exit_code == 1
    assert res.name == "poll"
    assert res.command == "poll"
    assert "No questions" in res.output

def test_empty_steps_list_errors(make_tool_call):
    call = make_tool_call("poll", args={"steps": []})
    res = execute_poll(call)
    assert res.status == "error"
    assert res.exit_code == 1

def test_legacy_question_options_builds_step(make_tool_call, stub_run_poll):
    captured = stub_run_poll([{"question": "Pick?", "answer": "A"}])
    call = make_tool_call(
        "poll", args={"question": "Pick?", "options": ["A", "B"]}
    )
    res = execute_poll(call)
    assert res.status == "ok"
    assert captured["steps"] == [{"question": "Pick?", "options": ["A", "B"]}]
    assert "Q: Pick?" in res.output
    assert "A: A" in res.output

def test_legacy_question_without_options(make_tool_call, stub_run_poll):
    captured = stub_run_poll([{"question": "Free?", "answer": "yes"}])
    call = make_tool_call("poll", args={"question": "Free?"})
    res = execute_poll(call)
    assert res.status == "ok"
    assert captured["steps"] == [{"question": "Free?", "options": []}]

def test_steps_passed_through(make_tool_call, stub_run_poll):
    steps = [
        {"question": "Q1", "options": ["x"]},
        {"question": "Q2", "options": ["y"]},
    ]
    captured = stub_run_poll(
        [
            {"question": "Q1", "answer": "x"},
            {"question": "Q2", "answer": "y"},
        ]
    )
    call = make_tool_call("poll", args={"steps": steps})
    res = execute_poll(call)
    assert res.status == "ok"
    assert captured["steps"] == steps

def test_output_shape_multiple(make_tool_call, stub_run_poll):
    stub_run_poll(
        [
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ]
    )
    call = make_tool_call("poll", args={"steps": [{"question": "Q1"}, {"question": "Q2"}]})
    res = execute_poll(call)
    assert res.exit_code == 0
    assert res.output == "Q: Q1\nA: A1\n\nQ: Q2\nA: A2"

def test_output_stripped_single(make_tool_call, stub_run_poll):
    stub_run_poll([{"question": "Only", "answer": "42"}])
    call = make_tool_call("poll", args={"steps": [{"question": "Only"}]})
    res = execute_poll(call)
    assert res.output == "Q: Only\nA: 42"
    assert not res.output.endswith("\n")

def test_limits_steps_to_ten(make_tool_call, stub_run_poll):
    steps = [{"question": f"Q{i}", "options": ["A"]} for i in range(12)]
    captured = stub_run_poll(lambda passed: [{"question": s["question"], "answer": "A"} for s in passed])
    res = execute_poll(make_tool_call("poll", args={"steps": steps}))
    assert res.status == "ok"
    assert len(captured["steps"]) == 10

def test_multi_select_answer_list_is_printed(make_tool_call, stub_run_poll):
    stub_run_poll([{"question": "Pick many?", "answer": ["A", "C"]}])
    res = execute_poll(make_tool_call("poll", args={"steps": [{"question": "Pick many?", "multiple": True}]}))
    assert res.output == "Q: Pick many?\nA: A, C"
