"""apis/agent_adapter.py — адаптация tool_calls / контента / usage.

Тестируются чистые функции-хелперы; провайдер/HTTP не вызываются.
"""

import json

import apis.agent_adapter as aa
from apis.messages import ToolMessage


class TestContentToText:
    def test_plain_string(self):
        assert aa._content_to_text("hello") == "hello"

    def test_none_returns_empty(self):
        assert aa._content_to_text(None) == ""

    def test_html_entities_unescaped(self):
        assert aa._content_to_text("a <b> & c") == "a <b> & c"

    def test_list_of_text_parts(self):
        content = [{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]
        assert aa._content_to_text(content) == "foobar"

    def test_list_mixed_str_and_dict(self):
        content = ["a", {"text": "b"}, {"no_text": 1}]
        assert aa._content_to_text(content) == "ab"

    def test_list_unescapes_joined(self):
        content = [{"text": "x & y"}]
        assert aa._content_to_text(content) == "x & y"

    def test_non_string_object_coerced(self):
        assert aa._content_to_text(123) == "123"

class TestToolCallsToTextBlocks:
    def test_shell_command(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "shell", "args": {"command": "ls -la"}},
        ])
        assert ":::call shell" in out
        assert "call:::" in out
        assert json.dumps({"command": "ls -la"}, ensure_ascii=False) in out

    def test_create_file_content_in_body(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "create_file", "args": {"path": "a.py", "content": "print(1)"}},
        ])
        assert ':::call create_file path="a.py"' in out
        assert "print(1)" in out

    def test_create_file_with_content(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "create_file", "args": {"path": "a.bin", "content": "x"}},
        ])
        assert ':::call create_file path="a.bin"' in out
        assert "x" in out

    def test_create_file_content_in_body_simple(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "create_file", "args": {"path": "new.txt", "content": "data"}},
        ])
        assert ':::call create_file path="new.txt"' in out
        assert "data" in out

    def test_generic_tool_json_body(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "read_files", "args": {"path": "x.py"}},
        ])
        assert ":::call read_files" in out
        assert json.dumps({"path": "x.py"}, ensure_ascii=False) in out

    def test_missing_name_defaults_to_shell(self):
        out = aa._tool_calls_to_text_blocks([{"args": {}}])
        assert ":::call shell" in out

    def test_unicode_preserved_not_escaped(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "read_files", "args": {"path": "файл.py"}},
        ])
        assert "файл.py" in out

    def test_multiple_calls_concatenated(self):
        out = aa._tool_calls_to_text_blocks([
            {"name": "shell", "args": {"command": "a"}},
            {"name": "shell", "args": {"command": "b"}},
        ])
        assert out.count(":::call shell") == 2

    def test_empty_list(self):
        assert aa._tool_calls_to_text_blocks([]) == ""

class TestEnsureToolCallIds:
    def test_adds_missing_id(self):
        out = aa._ensure_tool_call_ids([{"name": "shell"}])
        assert out[0]["id"].startswith("call_")

    def test_keeps_existing_id(self):
        out = aa._ensure_tool_call_ids([{"name": "shell", "id": "fixed"}])
        assert out[0]["id"] == "fixed"

    def test_does_not_mutate_input(self):
        src = [{"name": "shell"}]
        aa._ensure_tool_call_ids(src)
        assert "id" not in src[0]

    def test_ids_unique(self):
        out = aa._ensure_tool_call_ids([{"name": "a"}, {"name": "b"}])
        assert out[0]["id"] != out[1]["id"]

class TestStructuredResultContent:
    def test_basic(self):
        c = aa._structured_result_content({"command": "ls", "output": "file.txt"})
        assert c == "$ ls\nfile.txt"

    def test_name_fallback(self):
        c = aa._structured_result_content({"name": "read_files", "output": "data"})
        assert c.startswith("$ read_files")

    def test_no_command_default_tool(self):
        c = aa._structured_result_content({"output": "x"})
        assert c.startswith("$ tool")

    def test_nonzero_exit_code_in_header(self):
        c = aa._structured_result_content({"command": "bad", "output": "err", "exit_code": 2})
        assert "$ bad [exit 2]" in c

    def test_zero_exit_no_marker(self):
        c = aa._structured_result_content({"command": "ok", "output": "y", "exit_code": 0})
        assert "[exit" not in c

    def test_empty_output_returns_no_output(self):
        c = aa._structured_result_content({"command": "noop"})
        # header present, output empty → rstrip leaves just the header
        assert c == "$ noop"

    def test_completely_empty_returns_placeholder(self):
        c = aa._structured_result_content({})
        assert c == "$ tool"

    def test_html_entities_unescaped(self):
        c = aa._structured_result_content({"command": "echo", "output": "a & b"})
        assert "a & b" in c

class TestFirstInt:
    def test_returns_first_nonempty(self):
        assert aa._first_int({"a": 0, "b": 5}, "a", "b") == 5

    def test_string_coerced(self):
        assert aa._first_int({"a": "7"}, "a") == 7

    def test_missing_keys_zero(self):
        assert aa._first_int({}, "x", "y") == 0

    def test_invalid_value_skipped(self):
        assert aa._first_int({"a": "abc", "b": 3}, "a", "b") == 3

class _UsageObj:
    def __init__(self, usage_metadata=None, response_metadata=None):
        if usage_metadata is not None:
            self.usage_metadata = usage_metadata
        if response_metadata is not None:
            self.response_metadata = response_metadata

class TestExtractUsage:
    def test_usage_metadata_basic(self):
        obj = _UsageObj(usage_metadata={"input_tokens": 10, "output_tokens": 20})
        out = aa._extract_usage(obj)
        assert out["input"] == 10
        assert out["output"] == 20
        assert out["total"] == 30  # computed

    def test_usage_metadata_explicit_total(self):
        obj = _UsageObj(usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 99})
        assert aa._extract_usage(obj)["total"] == 99

    def test_usage_metadata_reasoning(self):
        obj = _UsageObj(usage_metadata={
            "input_tokens": 5, "output_tokens": 5,
            "output_token_details": {"reasoning": 3},
        })
        assert aa._extract_usage(obj)["reasoning"] == 3

    def test_response_metadata_openai_style(self):
        obj = _UsageObj(response_metadata={
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })
        out = aa._extract_usage(obj)
        assert out["input"] == 100
        assert out["output"] == 50
        assert out["total"] == 150

    def test_response_metadata_usage_key(self):
        obj = _UsageObj(response_metadata={
            "usage": {"input_tokens": 7, "output_tokens": 8},
        })
        out = aa._extract_usage(obj)
        assert out["input"] == 7
        assert out["output"] == 8
        assert out["total"] == 15

    def test_response_metadata_reasoning(self):
        obj = _UsageObj(response_metadata={
            "token_usage": {
                "prompt_tokens": 1, "completion_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 4},
            },
        })
        assert aa._extract_usage(obj)["reasoning"] == 4

    def test_empty_returns_empty_dict(self):
        assert aa._extract_usage(_UsageObj()) == {}

    def test_usage_metadata_preferred_over_response(self):
        obj = _UsageObj(
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
            response_metadata={"token_usage": {"prompt_tokens": 999}},
        )
        out = aa._extract_usage(obj)
        assert out["input"] == 1

class TestBuildNativeToolMessages:
    def test_one_message_per_call(self):
        pending = [{"name": "shell", "id": "c1"}, {"name": "read_files", "id": "c2"}]
        results = [
            {"name": "shell", "command": "ls", "output": "a"},
            {"name": "read_files", "command": "read", "output": "b"},
        ]
        out = aa.build_native_tool_messages(pending, results)
        assert len(out) == 2
        assert all(isinstance(m, ToolMessage) for m in out)
        assert out[0].tool_call_id == "c1"
        assert out[1].tool_call_id == "c2"

    def test_missing_result_gets_no_output(self):
        pending = [{"name": "shell", "id": "c1"}]
        out = aa.build_native_tool_messages(pending, [])
        assert out[0].content == "(no output)"

    def test_control_tool_ack_plan(self):
        pending = [{"name": "plan", "id": "p1"}]
        out = aa.build_native_tool_messages(pending, [])
        assert out[0].content == "(plan recorded)"

    def test_control_tool_ack_think(self):
        pending = [{"name": "think", "id": "t1"}]
        out = aa.build_native_tool_messages(pending, [])
        assert out[0].content == "(thought recorded)"

    def test_fifo_matching_by_name(self):
        pending = [{"name": "shell", "id": "c1"}, {"name": "shell", "id": "c2"}]
        results = [
            {"name": "shell", "command": "first", "output": "1"},
            {"name": "shell", "command": "second", "output": "2"},
        ]
        out = aa.build_native_tool_messages(pending, results)
        assert "first" in out[0].content
        assert "second" in out[1].content

    def test_leftover_results_appended_to_last(self):
        pending = [{"name": "shell", "id": "c1"}]
        results = [
            {"name": "shell", "command": "a", "output": "out-a"},
            {"name": "unmatched", "command": "b", "output": "out-b"},
        ]
        out = aa.build_native_tool_messages(pending, results)
        assert len(out) == 1
        assert "out-a" in out[0].content
        assert "out-b" in out[0].content
