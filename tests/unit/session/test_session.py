"""session/session.py — Session, история, costs, compress."""

from session.session import Session


class TestBasicLifecycle:
    def test_init_default(self, isolated_data):
        s = Session()
        assert s.id
        assert s.site == "api"
        assert s.title == ""
        assert s.messages == []
        assert s.dir.parent == isolated_data / "sessions"

    def test_init_custom(self, isolated_data):
        s = Session(session_id="myid", title="t", site="onlysq")
        assert s.id == "myid"
        assert s.title == "t"
        assert s.site == "onlysq"

    def test_id_format_default(self, isolated_data):
        s = Session()
        # YYYYMMDD_HHMMSS_uid6
        parts = s.id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8
        assert len(parts[1]) == 6
        assert len(parts[2]) == 6


class TestAddMessages:
    def test_add_user(self, isolated_data):
        s = Session()
        m = s.add_user_message("hi", model="gpt")
        assert m.role == "user"
        assert s.messages == [m]
        assert s.title.startswith("hi")

    def test_add_assistant(self, isolated_data):
        s = Session()
        m = s.add_assistant_message("answer", model="gpt", duration=1.5)
        assert m.role == "assistant"
        assert m.duration == 1.5

    def test_add_system(self, isolated_data):
        s = Session()
        m = s.add_system_message("sys")
        assert m.role == "system"

    def test_add_tool_result(self, isolated_data):
        s = Session()
        m = s.add_tool_result("output")
        assert m.role == "tool_result"


class TestSlug:
    def test_make_slug_latin(self):
        assert Session._make_slug("hello world") == "hello_world"

    def test_make_slug_cyrillic(self):
        result = Session._make_slug("привет мир")
        assert "привет" in result and "мир" in result

    def test_make_slug_punctuation(self):
        result = Session._make_slug("test? what!")
        assert "?" not in result and "!" not in result

    def test_make_slug_max_len(self):
        long = "abcdefghijklmnopqrstuvwxyz"
        result = Session._make_slug(long, max_len=10)
        assert len(result) <= 10

    def test_make_slug_empty_fallback(self):
        assert Session._make_slug("???") == "chat"


class TestAutoTitle:
    def test_first_user_sets_title(self, isolated_data):
        s = Session()
        s.add_user_message("hello world")
        assert s.title == "hello world"

    def test_title_keeps_full_text_normalized(self, isolated_data):
        s = Session()
        s.add_user_message("  hello\n   world  ")
        assert s.title == "hello world"

    def test_existing_title_preserved(self, isolated_data):
        s = Session(title="custom")
        s.add_user_message("hello")
        assert s.title == "custom"


class TestProperties:
    def test_models_used(self, isolated_data):
        s = Session()
        s.add_user_message("u", model="gpt")
        s.add_assistant_message("a", model="claude")
        s.add_assistant_message("b", model="gpt")
        assert s.models_used == ["gpt", "claude"]

    def test_last_model(self, isolated_data):
        s = Session()
        s.add_assistant_message("a", model="gpt")
        s.add_assistant_message("b", model="claude")
        assert s.last_model == "claude"

    def test_unknown_model_ignored(self, isolated_data):
        s = Session()
        s.add_assistant_message("a", model="unknown")
        s.add_assistant_message("b", model="gpt")
        assert s.models_used == ["gpt"]

    def test_total_duration(self, isolated_data):
        s = Session()
        s.add_assistant_message("a", model="gpt", duration=1.5)
        s.add_assistant_message("b", model="gpt", duration=2.5)
        assert s.total_duration == 4.0

    def test_message_count_only_user(self, isolated_data):
        s = Session()
        s.add_user_message("u1")
        s.add_assistant_message("a1", model="gpt")
        s.add_user_message("u2")
        assert s.message_count == 2


class TestBuildCompressText:
    def test_skips_system_and_tool_result(self, isolated_data):
        s = Session()
        s.add_system_message("sys")
        s.add_user_message("user msg")
        s.add_tool_result("tool out")
        s.add_assistant_message("answer", model="gpt")
        text = s.build_compress_text()
        assert "sys" not in text
        assert "tool out" not in text
        assert "user msg" in text
        assert "answer" in text

    def test_truncates_long_tool_block_in_assistant(self, isolated_data):
        s = Session()
        big_block = ":::call create_file path=\"x\"\n" + "x" * 1000 + "\ncall:::"
        s.add_assistant_message("text " + big_block, model="gpt")
        text = s.build_compress_text()
        assert "truncated" in text


class TestCompressReset:
    def test_compress_basic(self, isolated_data):
        s = Session()
        s.add_user_message("u")
        s.add_assistant_message("a", model="gpt")
        s.compress_reset("compressed summary")
        assert len(s.messages) == 2  # 2 system-сообщения
        assert all(m.role == "system" for m in s.messages)
        assert s._compressed_stats is not None
        assert s._compressed_stats["messages"] == 1  # один user


class TestSessionRename:
    def test_first_user_triggers_rename(self, isolated_data):
        s = Session()
        s.ensure_dir()
        old_id = s.id
        s.add_user_message("hello world test")
        assert s.id != old_id
        assert "hello" in s.id

    def test_second_user_no_rename(self, isolated_data):
        s = Session()
        s.ensure_dir()
        s.add_user_message("first")
        first_id = s.id
        s.add_user_message("second")
        assert s.id == first_id
