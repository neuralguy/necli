"""session/message.py — модель сообщения с tokens/usage."""

from session.message import Message


class TestMessageBasic:
    def test_minimal(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.model == ""
        assert m.tokens > 0
        assert m.timestamp > 0
        assert m.duration is None
        assert m.usage is None

    def test_explicit_tokens_used(self):
        m = Message(role="user", content="hello", tokens=42)
        assert m.tokens == 42

    def test_explicit_timestamp(self):
        m = Message(role="user", content="hi", timestamp=1234567.0)
        assert m.timestamp == 1234567.0


class TestUsage:
    def test_assistant_uses_output(self):
        usage = {"input": 100, "output": 50, "total": 150}
        m = Message(role="assistant", content="hi", usage=usage)
        assert m.tokens == 50

    def test_user_ignores_output_uses_tiktoken(self):
        usage = {"input": 100, "output": 50}
        m = Message(role="user", content="hello world", usage=usage)
        # User не должен брать tokens из usage.output, считает через tiktoken
        assert m.tokens > 0
        assert m.tokens != 50

    def test_empty_usage_treated_as_none(self):
        m = Message(role="assistant", content="x", usage={})
        assert m.usage is None

    def test_assistant_zero_output_falls_back_to_count(self):
        m = Message(role="assistant", content="hello world", usage={"input": 100, "output": 0})
        assert m.tokens > 0


class TestSerialization:
    def test_to_dict_minimal(self):
        m = Message(role="user", content="hi", timestamp=1.0)
        d = m.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hi"
        assert d["timestamp"] == 1.0
        assert "duration" not in d
        assert "usage" not in d

    def test_to_dict_with_duration(self):
        m = Message(role="assistant", content="x", duration=1.234)
        d = m.to_dict()
        assert d["duration"] == 1.23

    def test_to_dict_with_usage(self):
        usage = {"input": 10, "output": 5}
        m = Message(role="assistant", content="x", usage=usage)
        d = m.to_dict()
        assert d["usage"] == usage

    def test_from_dict_roundtrip(self):
        original = Message(
            role="assistant", content="hi", model="gpt-5",
            timestamp=100.0, tokens=42, duration=2.5,
            usage={"input": 10, "output": 5},
        )
        d = original.to_dict()
        restored = Message.from_dict(d)
        assert restored.role == "assistant"
        assert restored.content == "hi"
        assert restored.model == "gpt-5"
        assert restored.timestamp == 100.0
        assert restored.tokens == 42
        assert restored.duration == 2.5
        assert restored.usage == {"input": 10, "output": 5}

    def test_from_dict_missing_optional(self):
        d = {"role": "user", "content": "hi"}
        m = Message.from_dict(d)
        assert m.role == "user"
        assert m.duration is None
        assert m.usage is None
        assert m.timestamp > 0
