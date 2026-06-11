"""Тесты для hooks-системы (src/hooks + config/hooks + интеграция в execute_call)."""

import json

import pytest

from config import hooks as hooks_config
from hooks import run_hooks
from hooks.matcher import if_matches, matcher_matches


@pytest.fixture
def hooks_file(tmp_path, monkeypatch):
    """Подменяет HOOKS_FILE на временный и сбрасывает кэш."""
    from config import paths

    f = tmp_path / "hooks.json"
    monkeypatch.setattr(paths, "HOOKS_FILE", f)
    monkeypatch.setattr(hooks_config, "HOOKS_FILE", f)
    hooks_config.invalidate_cache()
    yield f
    hooks_config.invalidate_cache()


def _write(f, obj):
    f.write_text(json.dumps(obj), encoding="utf-8")
    hooks_config.invalidate_cache()


# ---------------- matcher ----------------

def test_matcher_wildcard():
    assert matcher_matches("*", "shell")
    assert matcher_matches("", "shell")
    assert matcher_matches("shell", "shell")
    assert not matcher_matches("write_file", "shell")


def test_matcher_alternation():
    assert matcher_matches("shell|write_file", "write_file")
    assert not matcher_matches("shell|read_files", "write_file")


def test_if_rule_tool_only():
    assert if_matches("shell", "shell", {})
    assert not if_matches("shell", "write_file", {})


def test_if_rule_with_arg_glob():
    assert if_matches("shell(git push *)", "shell", {"command": "git push origin main"})
    assert not if_matches("shell(git push *)", "shell", {"command": "ls -la"})


def test_if_rule_path_match():
    assert if_matches("write_file(*.py)", "write_file", {"path": "src/foo.py"})
    assert not if_matches("write_file(*.py)", "write_file", {"path": "README.md"})


def test_if_rule_empty_matches_all():
    assert if_matches(None, "shell", {})
    assert if_matches("", "shell", {})


# ---------------- config loading ----------------

def test_load_matcher_format(hooks_file):
    _write(hooks_file, {
        "PreToolUse": [
            {"matcher": "shell", "hooks": [{"type": "command", "command": "true"}]}
        ]
    })
    cfg = hooks_config.load_hooks()
    assert "PreToolUse" in cfg
    assert cfg["PreToolUse"][0].matcher == "shell"
    assert cfg["PreToolUse"][0].hooks[0].command == "true"


def test_load_flat_format(hooks_file):
    _write(hooks_file, {"Stop": [{"type": "command", "command": "echo hi"}]})
    cfg = hooks_config.load_hooks()
    assert cfg["Stop"][0].matcher == "*"
    assert cfg["Stop"][0].hooks[0].command == "echo hi"


def test_load_wrapped_hooks_key(hooks_file):
    _write(hooks_file, {"hooks": {"Stop": [{"type": "command", "command": "x"}]}})
    cfg = hooks_config.load_hooks()
    assert "Stop" in cfg


def test_unknown_event_ignored(hooks_file):
    _write(hooks_file, {"NotARealEvent": [{"type": "command", "command": "x"}]})
    cfg = hooks_config.load_hooks()
    assert cfg == {}


def test_missing_file(hooks_file):
    assert hooks_config.load_hooks() == {}
    assert not hooks_config.has_hooks()


# ---------------- runner: exit codes ----------------

def test_command_hook_block_exit2(hooks_file, tmp_path):
    _write(hooks_file, {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": "echo 'nope' >&2; exit 2"}]}
        ]
    })
    out = run_hooks("PreToolUse", {"tool_name": "shell"}, working_dir=str(tmp_path))
    assert out.blocked
    assert "nope" in out.block_reason


def test_command_hook_ok_no_block(hooks_file, tmp_path):
    _write(hooks_file, {
        "PreToolUse": [{"hooks": [{"type": "command", "command": "exit 0"}]}]
    })
    out = run_hooks("PreToolUse", {"tool_name": "shell"}, working_dir=str(tmp_path))
    assert not out.blocked


def test_command_hook_additional_context_plaintext(hooks_file, tmp_path):
    _write(hooks_file, {
        "PostToolUse": [{"hooks": [{"type": "command", "command": "echo extra-info"}]}]
    })
    out = run_hooks("PostToolUse", {"tool_name": "read_files"}, working_dir=str(tmp_path))
    assert "extra-info" in out.context_text


# ---------------- runner: JSON protocol ----------------

def test_command_hook_json_block(hooks_file, tmp_path):
    payload = json.dumps({"decision": "block", "reason": "policy violation"})
    _write(hooks_file, {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": f"echo '{payload}'"}]}
        ]
    })
    out = run_hooks("PreToolUse", {"tool_name": "shell"}, working_dir=str(tmp_path))
    assert out.blocked
    assert out.block_reason == "policy violation"


def test_command_hook_json_additional_context(hooks_file, tmp_path):
    payload = json.dumps({"additionalContext": "remember X"})
    _write(hooks_file, {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": f"echo '{payload}'"}]}
        ]
    })
    out = run_hooks("UserPromptSubmit", {}, working_dir=str(tmp_path))
    assert "remember X" in out.context_text


def test_command_hook_json_continue_false(hooks_file, tmp_path):
    payload = json.dumps({"continue": False, "systemMessage": "stopping"})
    _write(hooks_file, {
        "Stop": [{"hooks": [{"type": "command", "command": f"echo '{payload}'"}]}]
    })
    out = run_hooks("Stop", {}, working_dir=str(tmp_path))
    assert out.stop
    assert "stopping" in out.system_messages


# ---------------- runner: if-filter ----------------

def test_if_filter_skips_nonmatching(hooks_file, tmp_path):
    _write(hooks_file, {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": "exit 2", "if": "shell(git push *)"}]}
        ]
    })
    # Не git push → hook не срабатывает.
    out = run_hooks("PreToolUse", {"tool_name": "shell", "tool_input": {"command": "ls"}},
                    working_dir=str(tmp_path))
    assert not out.blocked
    # git push → срабатывает и блокирует.
    out2 = run_hooks("PreToolUse", {"tool_name": "shell", "tool_input": {"command": "git push origin"}},
                     working_dir=str(tmp_path))
    assert out2.blocked


def test_matcher_skips_other_tool(hooks_file, tmp_path):
    _write(hooks_file, {
        "PreToolUse": [
            {"matcher": "write_file", "hooks": [{"type": "command", "command": "exit 2"}]}
        ]
    })
    out = run_hooks("PreToolUse", {"tool_name": "shell"}, working_dir=str(tmp_path))
    assert not out.blocked


# ---------------- integration: execute_call ----------------

def test_execute_call_blocked_by_pretooluse(hooks_file, tmp_path, monkeypatch):
    from tools import _paths
    from tools.models import ToolCall
    from tools.registry import execute_call

    monkeypatch.setattr(_paths, "get_working_dir", lambda: str(tmp_path))
    _write(hooks_file, {
        "PreToolUse": [
            {"matcher": "shell", "hooks": [{"type": "command", "command": "exit 2"}]}
        ]
    })
    res = execute_call(ToolCall(command="rm -rf /", tool_name="shell"))
    assert res.status == "error"
    assert res.exit_code == 2


def test_execute_call_runs_when_no_hooks(hooks_file, tmp_path, monkeypatch):
    from tools import _paths
    from tools.models import ToolCall
    from tools.registry import execute_call

    monkeypatch.setattr(_paths, "get_working_dir", lambda: str(tmp_path))
    # Нет hooks → инструмент выполняется нормально.
    res = execute_call(ToolCall(command="echo hi", tool_name="shell"))
    assert res.status == "ok"
