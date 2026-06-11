"""session/storage.py — save/load/list."""

import json

from session.session import Session
from session.storage import (
    save, load, list_sessions, get_statistics,
    _recalc_model_cost,
)


class TestSaveLoad:
    def test_save_creates_files(self, isolated_data):
        s = Session()
        s.add_user_message("hi")
        s.add_assistant_message("hello", model="gpt")
        save(s)
        assert (s.dir / "history.json").exists()
        assert (s.dir / "summary.json").exists()

    def test_load_roundtrip(self, isolated_data):
        s = Session()
        s.add_user_message("hi")
        s.add_assistant_message("ans", model="gpt", duration=1.5)
        save(s)

        loaded = load(s.id)
        assert loaded is not None
        assert loaded.id == s.id
        assert loaded.title == s.title
        assert len(loaded.messages) == 2
        assert loaded.messages[0].content == "hi"
        assert loaded.messages[1].duration == 1.5

    def test_load_missing_returns_none(self, isolated_data):
        assert load("nonexistent_session_xyz") is None

    def test_load_by_prefix(self, isolated_data):
        s = Session(session_id="abc12345_uniqueid")
        s.add_user_message("hi")
        save(s)
        loaded = load("abc12345")
        assert loaded is not None
        assert loaded.id == "abc12345_uniqueid"

    def test_load_compressed_stats_restored(self, isolated_data):
        s = Session()
        s.add_user_message("u")
        s.compress_reset("summary text")
        save(s)
        loaded = load(s.id)
        assert loaded._compressed_stats is not None
        assert loaded._compressed_stats["messages"] == 1

    def test_load_pre_compress_backup_restored(self, isolated_data):
        s = Session()
        s.add_user_message("original message")
        s.compress_reset("summary")
        save(s)
        loaded = load(s.id)
        assert hasattr(loaded, "_pre_compress_messages")
        assert len(loaded._pre_compress_messages) == 1
        assert loaded._pre_compress_messages[0]["content"] == "original message"

    def test_load_corrupt_json_returns_none(self, isolated_data):
        sdir = isolated_data / "sessions" / "broken"
        sdir.mkdir()
        (sdir / "history.json").write_text("not json at all")
        assert load("broken") is None


class TestListSessions:
    def test_empty(self, isolated_data):
        assert list_sessions() == []

    def test_sorted_by_updated_desc(self, isolated_data):
        s1 = Session(session_id="aaa")
        s1.add_user_message("first")
        s1.updated_at = 1000.0
        save(s1)

        s2 = Session(session_id="bbb")
        s2.add_user_message("second")
        s2.updated_at = 2000.0
        save(s2)

        sessions = list_sessions()
        assert len(sessions) == 2
        assert sessions[0]["id"] == "bbb"
        assert sessions[1]["id"] == "aaa"

    def test_limit(self, isolated_data):
        for i in range(5):
            s = Session(session_id=f"id_{i}")
            s.add_user_message(f"m{i}")
            save(s)
        result = list_sessions(limit=3)
        assert len(result) == 3


class TestRecalcModelCost:
    def test_zero_tokens_zero_cost(self):
        cost = _recalc_model_cost("Claude Opus 4.6", {
            "input_tokens": 0, "output_tokens": 0,
        })
        assert cost == 0.0

    def test_unknown_model_zero_pricing(self):
        cost = _recalc_model_cost("definitely-unknown", {
            "input_tokens": 1000, "output_tokens": 500,
        })
        assert cost == 0.0


class TestGetStatistics:
    def test_empty(self, isolated_data):
        stats = get_statistics()
        assert stats["total_sessions"] == 0
        assert stats["total_messages"] == 0
        assert stats["total_cost"] == 0.0

    def test_counts_sessions(self, isolated_data):
        s = Session()
        s.add_user_message("u")
        s.add_assistant_message("a", model="gpt")
        save(s)
        stats = get_statistics()
        assert stats["total_sessions"] == 1
        assert stats["total_messages"] >= 1

    def test_days_filter(self, isolated_data):
        # Старая сессия > 5 дней
        old = Session(session_id="old_id")
        old.add_user_message("old")
        old.updated_at = 0.0  # very old
        save(old)

        # Сохранённые updated_at тоже надо подкрутить — save() переписал.
        # Перепишем summary.json напрямую.
        summary_path = old.dir / "summary.json"
        data = json.loads(summary_path.read_text())
        data["updated_at"] = 0.0
        summary_path.write_text(json.dumps(data))

        stats_5d = get_statistics(days=5)
        assert stats_5d["total_sessions"] == 0