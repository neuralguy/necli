"""commands/slash.py — _handle_slash dispatcher + /help listing."""

from unittest.mock import MagicMock, patch

import pytest

from commands.slash import _handle_slash, _normalize_cmd, SlashResult
from commands.registry import COMMANDS

@pytest.fixture
def session():
    s = MagicMock()
    s.id = "sess-1"
    s.message_count = 0
    s.messages = []
    return s

@pytest.fixture(autouse=True)
def mute_console():
    """Глушим вывод rich-console, чтобы тесты не печатали в stdout."""
    with patch("commands.slash.console") as c:
        yield c

def call(cmd, session, model="claude-test"):
    return _handle_slash(cmd, model, session, last_elapsed=None)

class TestSimpleFlagCommands:
    def test_new_sets_do_new(self, session):
        r = call("/new", session)
        assert isinstance(r, SlashResult)
        assert r.do_new is True
        assert r.handled is True

    def test_think_toggles(self, session):
        assert call("/think", session).toggle_think is True

    def test_tool_format_toggles(self, session):
        assert call("/tool_format", session).toggle_tool_format is True

    def test_reflect_sets_flag(self, session):
        assert call("/reflect", session).do_reflect is True

    def test_decompress_sets_flag(self, session):
        assert call("/decompress", session).do_decompress is True

class TestBranch:
    def test_branch_empty_session_not_set(self, session):
        session.message_count = 0
        r = call("/branch", session)
        assert r.do_branch is False

    def test_branch_with_messages(self, session):
        session.message_count = 3
        assert call("/branch", session).do_branch is True

class TestCompress:
    def test_compress_empty_not_set(self, session):
        session.message_count = 0
        assert call("/compress", session).do_compress is False

    def test_compress_with_messages(self, session):
        session.message_count = 5
        assert call("/compress", session).do_compress is True

class TestUndo:
    def test_undo_default_one(self, session):
        assert call("/undo", session).undo_n == 1

    def test_undo_explicit_n(self, session):
        assert call("/undo 3", session).undo_n == 3

    def test_undo_invalid_falls_back_to_one(self, session):
        assert call("/undo abc", session).undo_n == 1

    def test_undo_zero_becomes_one(self, session):
        assert call("/undo 0", session).undo_n == 1

class TestModelInfo:
    def test_model_command_not_handled_as_direct_switch(self, session):
        # /model ветки в _handle_slash нет — переключение модели идёт через /models меню.
        r = call("/model gpt-5", session, model="m-x")
        assert isinstance(r, SlashResult)
        assert r.handled is True
        assert r.switch_api_model is None

class TestSessionSwitch:
    def test_session_id_not_handled_as_direct_switch(self, session):
        # /session <id> ветки в _handle_slash нет — падает в unknown, switch не ставится.
        assert call("/session abc123", session).switch_session is None

    def test_session_blank_id_no_switch(self, session):
        assert call("/session   ", session).switch_session is None

class TestSessionsMenu:
    def test_sessions_empty_list(self, session):
        with patch("commands.slash.storage.list_sessions", return_value=[]):
            r = call("/sessions", session)
        assert r.switch_session is None

    def test_sessions_pick_other(self, session):
        listing = [{"id": "other"}, {"id": "sess-1"}]
        with patch("commands.slash.storage.list_sessions", return_value=listing), \
             patch("commands.slash.select_session_menu", return_value=0):
            r = call("/sessions", session)
        assert r.switch_session == "other"

    def test_sessions_pick_current_no_switch(self, session):
        listing = [{"id": "sess-1"}]
        with patch("commands.slash.storage.list_sessions", return_value=listing), \
             patch("commands.slash.select_session_menu", return_value=0):
            r = call("/sessions", session)
        assert r.switch_session is None

    def test_sessions_cancel_no_switch(self, session):
        listing = [{"id": "other"}]
        with patch("commands.slash.storage.list_sessions", return_value=listing), \
             patch("commands.slash.select_session_menu", return_value=None):
            r = call("/sessions", session)
        assert r.switch_session is None

class TestCd:
    def test_cd_no_arg_prints_cwd(self, session):
        r = call("/cd", session)
        assert r.change_dir is None

    def test_cd_valid_dir(self, session, tmp_path):
        r = call(f"/cd {tmp_path}", session)
        assert r.change_dir is not None

    def test_cd_invalid_dir(self, session):
        r = call("/cd /no/such/dir/xyz123", session)
        assert r.change_dir is None

class TestCopy:
    def test_copy_no_assistant_messages(self, session):
        session.messages = []
        r = call("/copy", session)
        assert r.handled is True

    def test_copy_one_message(self, session):
        m = MagicMock(role="assistant", content="hello")
        session.messages = [m]
        with patch("ui.clipboard_copy.copy_to_clipboard", return_value=None) as cb:
            call("/copy", session)
        cb.assert_called_once_with("hello")

    def test_copy_multiple_joined(self, session):
        m1 = MagicMock(role="assistant", content="a")
        m2 = MagicMock(role="assistant", content="b")
        session.messages = [m1, m2]
        with patch("ui.clipboard_copy.copy_to_clipboard", return_value=None) as cb:
            call("/copy 2", session)
        payload = cb.call_args[0][0]
        assert "a" in payload and "b" in payload

class TestUnknown:
    def test_unknown_command_prints_hint(self, session, mute_console):
        r = call("/totally_bogus", session)
        assert isinstance(r, SlashResult)
        assert r.handled is True
        assert r.do_new is False
        assert r.switch_session is None
        mute_console.print.assert_called()

class TestHelp:
    def test_help_returns_handled_result(self, session):
        r = call("/help", session)
        assert isinstance(r, SlashResult)
        assert r.handled is True

    def test_help_prints_every_command_label(self, session, mute_console):
        call("/help", session)
        printed = " ".join(
            str(a) for c in mute_console.print.call_args_list for a in c.args
        )
        for cmd in COMMANDS:
            assert cmd.name in printed, f"{cmd.name} missing from /help output"

class TestNormalizeCmd:
    def test_canonical_name_and_rest(self):
        head, rest = _normalize_cmd("/undo 5")
        assert head == "/undo"
        assert rest == "5"

    def test_unknown_passthrough(self):
        head, rest = _normalize_cmd("/bogus arg")
        assert head == "/bogus"
        assert rest == "arg"

    def test_no_rest(self):
        head, rest = _normalize_cmd("/new")
        assert head == "/new"
        assert rest == ""