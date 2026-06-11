"""agent/render_store.py — хранилище рендер-событий сессии."""

from pathlib import Path


from agent import render_store
from agent.render_store import RenderItem, RenderStore
from tools.models import ToolCall, ToolResult

class TestRenderItem:
    def test_to_dict_roundtrip(self):
        it = RenderItem(kind="user", payload={"text": "hi"}, ts=123.0)
        d = it.to_dict()
        assert d == {"kind": "user", "payload": {"text": "hi"}, "ts": 123.0}
        back = RenderItem.from_dict(d)
        assert back.kind == "user"
        assert back.payload == {"text": "hi"}
        assert back.ts == 123.0

    def test_from_dict_defaults(self):
        it = RenderItem.from_dict({})
        assert it.kind == "unknown"
        assert it.payload == {}
        assert isinstance(it.ts, float)

    def test_from_dict_none_payload(self):
        it = RenderItem.from_dict({"kind": "x", "payload": None})
        assert it.payload == {}

class TestAddMethods:
    def setup_method(self):
        self.store = RenderStore()

    def test_add_returns_item_and_appends(self):
        it = self.store.add("k", {"a": 1})
        assert isinstance(it, RenderItem)
        assert len(self.store) == 1
        assert self.store.items[0] is it

    def test_add_user(self):
        it = self.store.add_user("hello", status="done")
        assert it.kind == "user"
        assert it.payload == {"text": "hello", "status": "done"}

    def test_add_user_empty(self):
        it = self.store.add_user(None)
        assert it.payload == {"text": "", "status": ""}

    def test_add_assistant_block(self):
        it = self.store.add_assistant_block("body", subtitle="sub", message_num=3)
        assert it.kind == "assistant"
        assert it.payload == {"text": "body", "subtitle": "sub", "message_num": 3}

    def test_add_plan(self):
        it = self.store.add_plan({"goal": "g"})
        assert it.kind == "plan"
        assert it.payload == {"plan": {"goal": "g"}}

    def test_add_think_filters_empty(self):
        it = self.store.add_think(["a", "", "b", None])
        assert it.payload == {"steps": ["a", "b"]}

    def test_add_tool_with_call_and_result(self):
        call = ToolCall(command="ls", tool_name="ls", args={"path": "."}, raw="raw")
        res = ToolResult(name="ls", status="ok", output="out", exit_code=0)
        it = self.store.add_tool(call, res, subtitle="s")
        assert it.kind == "tool"
        assert it.payload["call"]["tool_name"] == "ls"
        assert it.payload["result"]["status"] == "ok"
        assert it.payload["subtitle"] == "s"

    def test_add_tool_none(self):
        it = self.store.add_tool(None, None)
        assert it.payload["call"] is None
        assert it.payload["result"] is None

    def test_add_command_only(self):
        call = ToolCall(command="rm", tool_name="shell")
        it = self.store.add_command_only(call, subtitle="x")
        assert it.kind == "command_only"
        assert it.payload["call"]["command"] == "rm"

class TestSerializeToolCall:
    def test_roundtrip(self):
        call = ToolCall(command="echo hi", tool_name="shell", args={"k": "v"}, raw="r")
        d = render_store._serialize_tool_call(call)
        assert d == {"command": "echo hi", "tool_name": "shell", "args": {"k": "v"}, "raw": "r"}
        back = render_store.deserialize_tool_call(d)
        assert back.command == "echo hi"
        assert back.tool_name == "shell"
        assert back.args == {"k": "v"}
        assert back.raw == "r"

    def test_deserialize_defaults(self):
        back = render_store.deserialize_tool_call({})
        assert back.command == ""
        assert back.tool_name == ""
        assert back.args == {}

class TestSerializeToolResult:
    def test_roundtrip_basic(self):
        res = ToolResult(name="sh", status="error", output="boom", exit_code=2, command="cmd")
        res.elapsed = 1.5
        d = render_store._serialize_tool_result(res)
        assert d["name"] == "sh"
        assert d["status"] == "error"
        assert d["output"] == "boom"
        assert d["exit_code"] == 2
        assert d["command"] == "cmd"
        assert d["elapsed"] == 1.5
        back = render_store.deserialize_tool_result(d)
        assert back.name == "sh"
        assert back.status == "error"
        assert back.exit_code == 2
        assert back.elapsed == 1.5

    def test_flags_preserved(self):
        res = ToolResult(name="r", status="ok", output="")
        res.full_content = True
        res.fatal = True
        d = render_store._serialize_tool_result(res)
        assert d["full_content"] is True
        assert d["fatal"] is True
        back = render_store.deserialize_tool_result(d)
        assert back.full_content is True
        assert back.fatal is True

    def test_image_paths_serialized(self):
        res = ToolResult(name="r", status="ok", output="")
        res.image_path = Path("/tmp/a.png")
        res.image_paths = [Path("/tmp/a.png"), Path("/tmp/b.png")]
        d = render_store._serialize_tool_result(res)
        assert d["image_path"] == "/tmp/a.png"
        assert d["image_paths"] == ["/tmp/a.png", "/tmp/b.png"]
        back = render_store.deserialize_tool_result(d)
        assert back.image_path == Path("/tmp/a.png")
        assert back.image_paths == [Path("/tmp/a.png"), Path("/tmp/b.png")]

    def test_line_starts_serialized(self):
        res = ToolResult(name="r", status="ok", output="")
        res.line_starts = [1, 5, 10]
        d = render_store._serialize_tool_result(res)
        assert d["line_starts"] == [1, 5, 10]
        back = render_store.deserialize_tool_result(d)
        assert back.line_starts == [1, 5, 10]

    def test_no_image_no_key(self):
        res = ToolResult(name="r", status="ok", output="")
        d = render_store._serialize_tool_result(res)
        assert "image_path" not in d
        assert "image_paths" not in d
        assert "line_starts" not in d

class TestStoreDictRoundtrip:
    def test_to_dict_structure(self):
        store = RenderStore()
        store.add_user("hi")
        store.add_plan({"x": 1})
        d = store.to_dict()
        assert d["version"] == render_store._VERSION
        assert len(d["items"]) == 2
        assert d["items"][0]["kind"] == "user"

    def test_from_dict_roundtrip(self):
        store = RenderStore()
        store.add_user("a")
        store.add_assistant_block("b")
        restored = RenderStore.from_dict(store.to_dict())
        assert len(restored) == 2
        assert restored.items[0].kind == "user"
        assert restored.items[1].kind == "assistant"

    def test_from_dict_invalid_input(self):
        assert len(RenderStore.from_dict(None)) == 0
        assert len(RenderStore.from_dict("nope")) == 0

    def test_from_dict_skips_non_dict_items(self):
        restored = RenderStore.from_dict({"items": [{"kind": "user"}, "bad", 42]})
        assert len(restored) == 1
        assert restored.items[0].kind == "user"

    def test_from_dict_missing_items(self):
        assert len(RenderStore.from_dict({})) == 0

class TestStoreOps:
    def test_clear(self):
        store = RenderStore()
        store.add_user("x")
        store.add_user("y")
        assert len(store) == 2
        store.clear()
        assert len(store) == 0

    def test_len(self):
        store = RenderStore()
        assert len(store) == 0
        store.add_user("x")
        assert len(store) == 1