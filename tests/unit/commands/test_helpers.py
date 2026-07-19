"""commands/helpers.py — чистые helper-функции."""

import time

from commands.helpers import _format_relative_time, _read_version


class TestFormatRelativeTime:
    def test_zero_returns_empty(self):
        assert _format_relative_time(0) == ""

    def test_falsy_returns_empty(self):
        assert _format_relative_time(None) == ""  # type: ignore[arg-type]

    def test_just_now(self):
        assert _format_relative_time(time.time()) == "just now"

    def test_minutes_ago(self):
        ts = time.time() - 5 * 60
        assert _format_relative_time(ts) == "5m ago"

    def test_hours_ago(self):
        ts = time.time() - 3 * 3600
        assert _format_relative_time(ts) == "3h ago"

    def test_days_ago(self):
        ts = time.time() - 2 * 86400
        assert _format_relative_time(ts) == "2d ago"

    def test_weeks_ago(self):
        ts = time.time() - 3 * 7 * 86400
        assert _format_relative_time(ts) == "3w ago"

    def test_months_ago(self):
        ts = time.time() - 90 * 86400
        assert _format_relative_time(ts) == "3mo ago"

    def test_boundary_just_under_minute(self):
        ts = time.time() - 59
        assert _format_relative_time(ts) == "just now"

    def test_boundary_just_under_hour(self):
        ts = time.time() - 59 * 60
        assert _format_relative_time(ts) == "59m ago"

    def test_returns_string(self):
        assert isinstance(_format_relative_time(time.time() - 100), str)

class TestReadVersion:
    def test_returns_non_empty_string(self):
        v = _read_version()
        assert isinstance(v, str)
        assert v != ""

    def test_looks_like_version_or_fallback(self):
        v = _read_version()
        # either a dotted version or the documented "0.0.0" fallback
        assert any(ch.isdigit() for ch in v)

    def test_matches_pyproject_version(self):
        """Версия должна совпадать с [project].version в pyproject.toml,
        даже когда пакет не установлен (importlib.metadata падает) —
        регрессия на баг, когда показывался 0.0.0."""
        import re
        from pathlib import Path

        # tests/unit/commands/test_helpers.py → корень репо (^4).
        root = Path(__file__).resolve().parents[3]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r"^\s*\[project\]\s*$(.*?)(^\s*\[|\Z)", text, re.M | re.S)
        assert m, "no [project] table in pyproject.toml"
        vm = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', m.group(1), re.M)
        assert vm, "no version in [project]"
        expected = vm.group(1)

        assert _read_version() == expected
        assert _read_version() != "0.0.0"
