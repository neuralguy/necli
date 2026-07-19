"""apis/messages.py — классы сообщений + AIMessageChunk.__add__."""

from apis.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    _merge_tool_call_chunks,
    _tc_chunks_to_tool_calls,
)


class TestBasic:
    def test_human_message(self):
        m = HumanMessage(content="hi")
        assert m.role == "user"
        assert m.content == "hi"

    def test_system_message(self):
        m = SystemMessage(content="sys")
        assert m.role == "system"

    def test_tool_message_defaults(self):
        m = ToolMessage(content="out", tool_call_id="t1")
        assert m.role == "tool"
        assert m.tool_call_id == "t1"
        assert m.name == "tool"

    def test_tool_message_with_name(self):
        m = ToolMessage(content="out", tool_call_id="t1", name="read_files")
        assert m.name == "read_files"

    def test_ai_message_defaults(self):
        m = AIMessage(content="ans")
        assert m.role == "assistant"
        assert m.tool_calls == []
        assert m.usage_metadata == {}

    def test_ai_message_with_tools(self):
        tc = [{"id": "1", "name": "shell", "args": {}, "type": "tool_call"}]
        m = AIMessage(content="", tool_calls=tc)
        assert m.tool_calls == tc

    def test_repr_short(self):
        assert "hi" in repr(HumanMessage(content="hi"))

    def test_repr_long_truncated(self):
        long = "x" * 200
        r = repr(HumanMessage(content=long))
        assert "..." in r


class TestAdditionalKwargs:
    def test_base_additional_kwargs(self):
        m = BaseMessage(content="x", additional_kwargs={"foo": "bar"})
        assert m.additional_kwargs == {"foo": "bar"}

    def test_default_empty(self):
        m = BaseMessage(content="x")
        assert m.additional_kwargs == {}
        assert m.response_metadata == {}


class TestChunkAdd:
    def test_content_concatenation(self):
        a = AIMessageChunk(content="Hello ")
        b = AIMessageChunk(content="world")
        merged = a + b
        assert merged.content == "Hello world"

    def test_non_chunk_addition_raises(self):
        import pytest

        a = AIMessageChunk(content="A")
        with pytest.raises(TypeError):
            a + "not a chunk"

    def test_reasoning_concat(self):
        a = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think "})
        b = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "more"})
        merged = a + b
        assert merged.additional_kwargs["reasoning_content"] == "think more"

    def test_other_kwargs_overwrite(self):
        a = AIMessageChunk(content="", additional_kwargs={"foo": "old"})
        b = AIMessageChunk(content="", additional_kwargs={"foo": "new"})
        merged = a + b
        assert merged.additional_kwargs["foo"] == "new"

    def test_response_metadata_overwrite(self):
        a = AIMessageChunk(content="", response_metadata={"x": 1, "y": 2})
        b = AIMessageChunk(content="", response_metadata={"y": 20, "z": 3})
        merged = a + b
        assert merged.response_metadata == {"x": 1, "y": 20, "z": 3}

    def test_usage_latest_nonempty(self):
        a = AIMessageChunk(content="", usage_metadata={"output_tokens": 1})
        b = AIMessageChunk(content="", usage_metadata={"output_tokens": 5})
        merged = a + b
        assert merged.usage_metadata == {"output_tokens": 5}

    def test_usage_empty_keeps_previous(self):
        a = AIMessageChunk(content="", usage_metadata={"output_tokens": 1})
        b = AIMessageChunk(content="")
        merged = a + b
        assert merged.usage_metadata == {"output_tokens": 1}

    def test_tool_call_chunks_accumulate(self):
        a = AIMessageChunk(
            content="",
            tool_call_chunks=[{"index": 0, "id": "t1", "name": "shell", "args": '{"c'}],
        )
        b = AIMessageChunk(
            content="",
            tool_call_chunks=[{"index": 0, "args": 'ommand": "ls"}'}],
        )
        merged = a + b
        # tool_call_chunks склеились в один по index=0
        assert len(merged.tool_call_chunks) == 1
        ch = merged.tool_call_chunks[0]
        assert ch["id"] == "t1"
        assert ch["name"] == "shell"
        assert ch["args"] == '{"command": "ls"}'
        # И tool_calls финальные — с распарсенными args
        assert merged.tool_calls
        assert merged.tool_calls[0]["args"] == {"command": "ls"}


class TestMergeToolCallChunks:
    def test_first_nonempty_id_kept(self):
        a = [{"index": 0, "id": "real_id", "name": "tool", "args": "{}"}]
        b = [{"index": 0, "args": ""}]
        merged = _merge_tool_call_chunks(a, b)
        assert merged[0]["id"] == "real_id"
        assert merged[0]["name"] == "tool"

    def test_different_indexes_separate(self):
        a = [{"index": 0, "name": "first", "args": "{}"}]
        b = [{"index": 1, "name": "second", "args": "{}"}]
        merged = _merge_tool_call_chunks(a, b)
        assert len(merged) == 2

    def test_args_string_concat(self):
        a = [{"index": 0, "args": "{"}]
        b = [{"index": 0, "args": "}"}]
        merged = _merge_tool_call_chunks(a, b)
        assert merged[0]["args"] == "{}"


class TestTcChunksToToolCalls:
    def test_valid_json_args(self):
        chunks = [{"index": 0, "id": "t1", "name": "shell", "args": '{"command": "ls"}'}]
        result = _tc_chunks_to_tool_calls(chunks)
        assert result == [{"id": "t1", "name": "shell", "args": {"command": "ls"}, "type": "tool_call"}]

    def test_empty_args_string(self):
        chunks = [{"index": 0, "id": "t1", "name": "shell", "args": ""}]
        result = _tc_chunks_to_tool_calls(chunks)
        assert result[0]["args"] == {}

    def test_invalid_json_preserves_raw_error(self):
        chunks = [{"index": 0, "id": "t1", "name": "shell", "args": "not json"}]
        result = _tc_chunks_to_tool_calls(chunks)
        assert result[0]["args"]["_invalid_json"] is True
        assert result[0]["args"]["_raw_args"] == "not json"
        assert "Expecting value" in result[0]["args"]["_parse_error"]

    def test_dict_args_passthrough(self):
        chunks = [{"index": 0, "id": "t1", "name": "shell", "args": {"x": 1}}]
        result = _tc_chunks_to_tool_calls(chunks)
        assert result[0]["args"] == {"x": 1}
