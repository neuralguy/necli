"""Проверки сборки системного промпта и разделённых prompt-модулей."""

import pytest

from prompts import fenced, native
from system_prompt import build_system_prompt, build_tool_results


def _build(**kwargs):
    kwargs.setdefault("native_tools", False)
    kwargs.setdefault("think_enabled", False)
    return build_system_prompt(**kwargs)


class TestBuildSystemPrompt:
    def test_returns_non_empty_string(self):
        result = _build()
        assert isinstance(result, str)
        assert len(result) > 1000

    def test_includes_core_sections(self):
        result = _build()
        for anchor in (
            "You are a Necli - terminal agent.",
            "# Tool call format",
            "# Response structure",
            "# Outcome discipline",
            "# Hard constraints",
        ):
            assert anchor in result

    def test_fenced_prompt_scales_process_to_task_size(self):
        result = _build(native_tools=False)
        assert "Match the process to the task size" in result
        assert "Do NOT install dependencies unless the user asks" in result

    def test_native_prompt_scales_process_to_task_size(self):
        result = _build(native_tools=True)
        assert "Match the process to the task size" in result
        assert "Do NOT install dependencies unless the user asks" in result

    def test_fenced_mode_has_call_markers(self):
        result = _build(native_tools=False)
        assert ":::call" in result
        assert "call:::" in result
        assert "# Tool call format: text mode" in result

    def test_native_mode_has_no_fenced_markers(self):
        result = _build(native_tools=True)
        authored = result.split("<persistent_memory>")[0]
        assert ":::call" not in authored
        assert "call:::" not in authored

    def test_subagent_prompt_omits_subagent_section(self):
        main = _build(for_subagent=False)
        subagent = _build(for_subagent=True)
        assert "# Subagents" in main
        assert "# Subagents" not in subagent
        assert len(subagent) < len(main)

    def test_environment_is_included(self):
        result = _build(working_dir="/tmp/some-dir")
        assert "# Environment" in result
        assert "/tmp/some-dir" in result

    def test_think_section_is_conditional(self):
        assert "# Think format" not in _build(think_enabled=False)
        assert "# Think format" in _build(think_enabled=True)


class TestPromptModules:
    @pytest.mark.parametrize("module", [native, fenced])
    def test_base_is_non_empty(self, module):
        assert module.BASE.strip()

    def test_native_base_has_no_fenced_markers(self):
        assert ":::call" not in native.BASE
        assert "call:::" not in native.BASE

    def test_fenced_base_has_call_markers(self):
        assert ":::call" in fenced.BASE
        assert "call:::" in fenced.BASE


class TestBuildToolResults:
    def test_empty_list(self):
        out = build_tool_results([])
        assert "<runtime_tool_results" in out
        assert "</runtime_tool_results>" in out

    def test_single_result_header_and_output(self):
        out = build_tool_results([{"command": "ls", "exit_code": 0, "output": "file.txt"}])
        assert 'command="ls"' in out
        assert "file.txt" in out
        assert "exit_code" not in out

    def test_non_zero_exit_in_header(self):
        out = build_tool_results([{"command": "false", "exit_code": 1, "output": ""}])
        assert 'command="false"' in out
        assert 'exit_code="1"' in out
