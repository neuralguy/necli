"""config/permissions.py — иерархия allow/deny session>process>forever."""

import pytest

from config.permissions import (
    get_decision, set_decision, reset_tool, reset_session, reset_all,
    get_scope,
)


@pytest.fixture(autouse=True)
def _isolated(isolated_data):
    reset_all()
    yield
    reset_all()


class TestGetDefault:
    def test_default_is_ask(self):
        assert get_decision("shell") == "ask"

    def test_no_scope_default(self):
        assert get_scope("shell") is None


class TestSetForever:
    def test_set_and_get(self):
        set_decision("shell", "allow", "forever")
        assert get_decision("shell") == "allow"
        assert get_scope("shell") == "forever"

    def test_deny(self):
        set_decision("write_file", "deny", "forever")
        assert get_decision("write_file") == "deny"


class TestSetProcess:
    def test_overrides_forever(self):
        set_decision("shell", "deny", "forever")
        set_decision("shell", "allow", "process")
        assert get_decision("shell") == "allow"
        assert get_scope("shell") == "process"


class TestSetSession:
    def test_overrides_process(self):
        set_decision("shell", "deny", "process")
        set_decision("shell", "allow", "session")
        assert get_decision("shell") == "allow"
        assert get_scope("shell") == "session"

    def test_overrides_forever_too(self):
        set_decision("shell", "deny", "forever")
        set_decision("shell", "allow", "session")
        assert get_decision("shell") == "allow"


class TestWildcard:
    def test_star_forever_fallback(self):
        set_decision("*", "allow", "forever")
        assert get_decision("anything") == "allow"

    def test_explicit_overrides_star(self):
        set_decision("*", "allow", "forever")
        set_decision("shell", "deny", "forever")
        assert get_decision("shell") == "deny"
        assert get_decision("other") == "allow"

    def test_session_star_beats_forever_explicit(self):
        set_decision("shell", "deny", "forever")
        set_decision("*", "allow", "session")
        # explicit forever на shell должен побеждать звезду на любом уровне
        # т.к. iteration: tool in SESSION? нет, tool in PROCESS? нет, tool in forever? да → deny
        assert get_decision("shell") == "deny"


class TestReset:
    def test_reset_tool_clears_all_levels(self):
        set_decision("shell", "allow", "forever")
        set_decision("shell", "deny", "process")
        set_decision("shell", "allow", "session")
        reset_tool("shell")
        assert get_decision("shell") == "ask"

    def test_reset_session_only(self):
        set_decision("shell", "allow", "forever")
        set_decision("shell", "deny", "session")
        reset_session()
        assert get_decision("shell") == "allow"

    def test_set_ask_clears_level(self):
        set_decision("shell", "allow", "session")
        set_decision("shell", "ask", "session")
        # session очищен, fallback на ask
        assert get_decision("shell") == "ask"


