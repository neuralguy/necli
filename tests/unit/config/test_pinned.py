"""config/pinned.py — round-trip закрепления сессий в pinned_sessions.json."""

import json

import pytest

from config import pinned

@pytest.fixture(autouse=True)
def _isolated(isolated_data, monkeypatch):
    monkeypatch.setattr(pinned, "_PATH", isolated_data / "pinned_sessions.json", raising=False)
    yield

class TestEmpty:
    def test_get_pinned_empty(self):
        assert pinned.get_pinned() == set()

class TestToggle:
    def test_toggle_on_returns_true(self):
        assert pinned.toggle("s1") is True
        assert "s1" in pinned.get_pinned()

    def test_toggle_off_returns_false(self):
        pinned.toggle("s1")
        assert pinned.toggle("s1") is False
        assert "s1" not in pinned.get_pinned()

    def test_toggle_independent_ids(self):
        pinned.toggle("a")
        pinned.toggle("b")
        assert pinned.get_pinned() == {"a", "b"}
        pinned.toggle("a")
        assert pinned.get_pinned() == {"b"}

class TestPersistence:
    def test_written_to_disk(self):
        pinned.toggle("s1")
        data = json.loads(pinned._PATH.read_text(encoding="utf-8"))
        assert data == ["s1"]

    def test_roundtrip_via_load(self):
        pinned.toggle("x")
        pinned.toggle("y")
        # свежее чтение с диска
        assert pinned.get_pinned() == {"x", "y"}

    def test_sorted_on_disk(self):
        pinned.toggle("zeta")
        pinned.toggle("alpha")
        data = json.loads(pinned._PATH.read_text(encoding="utf-8"))
        assert data == sorted(data)

class TestCorruptFile:
    def test_non_list_json_returns_empty(self):
        pinned._PATH.write_text('{"not": "a list"}', encoding="utf-8")
        assert pinned.get_pinned() == set()

    def test_invalid_json_returns_empty(self):
        pinned._PATH.write_text("not json at all", encoding="utf-8")
        assert pinned.get_pinned() == set()

    def test_recovers_after_corrupt(self):
        pinned._PATH.write_text("garbage", encoding="utf-8")
        assert pinned.toggle("s1") is True
        assert pinned.get_pinned() == {"s1"}