"""agent/events.py — протокол событий, NullEventHandler, RichEventHandler."""

import logging

from agent.events import (
    AgentEventHandler,
    NullEventHandler,
    RichEventHandler,
)

class TestProtocol:
    def test_null_handler_satisfies_protocol(self):
        assert isinstance(NullEventHandler(), AgentEventHandler)

    def test_rich_handler_satisfies_protocol(self):
        assert isinstance(RichEventHandler(), AgentEventHandler)

    def test_plain_object_does_not_satisfy(self):
        class Empty:
            pass

        assert not isinstance(Empty(), AgentEventHandler)

class TestNullEventHandler:
    def test_callbacks_are_noops(self, make_tool_call):
        h = NullEventHandler()
        call = make_tool_call("ls")
        # Ни один вызов не должен бросать.
        h.on_tool_start(call)
        h.on_tool_start(call, subtitle="sub")
        h.on_tool_result(object())
        h.on_plan_update(object())
        h.on_subagent_start(0, 1, "agent", "prompt")
        h.on_subagent_start(0, 1, "agent", "prompt", model_label="m")
        h.on_subagent_status(0, "msg")
        h.on_subagent_done(0)
        h.on_subagent_done(0, result="r")

    def test_on_status_logs_debug(self, caplog):
        h = NullEventHandler()
        with caplog.at_level(logging.DEBUG, logger="agent.events"):
            h.on_status("hello", level="warning")
        assert "hello" in caplog.text
        assert "warning" in caplog.text

class TestRichEventHandler:
    def test_on_tool_start_stores_pending(self, make_tool_call):
        h = RichEventHandler()
        call = make_tool_call("ls")
        h.on_tool_start(call, subtitle="sub")
        assert h._pending_call is call
        assert h._pending_subtitle == "sub"

    def test_on_tool_result_clears_pending(self, make_tool_call, monkeypatch):
        import agent.display as display

        captured = {}

        def fake_combined(call, result, subtitle=""):
            captured["call"] = call
            captured["result"] = result
            captured["subtitle"] = subtitle

        monkeypatch.setattr(display, "show_tool_combined", fake_combined)

        h = RichEventHandler()
        call = make_tool_call("ls")
        h.on_tool_start(call, subtitle="sub")
        result = object()
        h.on_tool_result(result)

        assert captured["call"] is call
        assert captured["result"] is result
        assert captured["subtitle"] == "sub"
        assert h._pending_call is None
        assert h._pending_subtitle == ""

    def test_on_tool_result_without_pending_uses_show_output(self, monkeypatch):
        import agent.display as display

        captured = {}
        monkeypatch.setattr(
            display, "show_output", lambda result: captured.setdefault("result", result)
        )

        h = RichEventHandler()
        result = object()
        h.on_tool_result(result)
        assert captured["result"] is result

    def test_on_plan_update_noop(self):
        h = RichEventHandler()
        h.on_plan_update(object())

    def test_on_status_prints(self):
        h = RichEventHandler()
        import io
        from rich.console import Console

        buf = io.StringIO()
        h._console = Console(file=buf, width=80, force_terminal=False)
        h.on_status("a status message", level="error")
        assert "a status message" in buf.getvalue()

    def test_on_status_unknown_level_defaults(self):
        h = RichEventHandler()
        import io
        from rich.console import Console

        buf = io.StringIO()
        h._console = Console(file=buf, width=80, force_terminal=False)
        h.on_status("msg", level="bogus")
        assert "msg" in buf.getvalue()

    def test_subagent_callbacks_delegate(self, monkeypatch):
        import agent.display as display

        calls = []
        monkeypatch.setattr(
            display, "show_subagent_start",
            lambda *a, **k: calls.append(("start", a, k)),
        )
        monkeypatch.setattr(
            display, "show_subagent_status",
            lambda *a, **k: calls.append(("status", a, k)),
        )
        monkeypatch.setattr(
            display, "show_subagent_done",
            lambda *a, **k: calls.append(("done", a, k)),
        )

        h = RichEventHandler()
        h.on_subagent_start(0, 2, "agent", "prompt", model_label="m")
        h.on_subagent_status(0, "running")
        h.on_subagent_done(0, result="ok")

        kinds = [c[0] for c in calls]
        assert kinds == ["start", "status", "done"]