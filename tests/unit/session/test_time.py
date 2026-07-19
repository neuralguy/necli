"""session/_time.py — форматирование MSK timestamps."""

import time
from datetime import datetime, timedelta, timezone

from session._time import MSK, format_msk, format_msk_short, format_relative


class TestMSK:
    def test_offset_is_3h(self):
        assert MSK.utcoffset(None) == timedelta(hours=3)


class TestFormatMsk:
    def test_known_timestamp(self):
        # 2024-01-01 00:00:00 UTC = 2024-01-01 03:00:00 MSK
        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        assert format_msk(ts) == "2024-01-01 03:00:00"

    def test_short(self):
        ts = datetime(2024, 6, 15, 9, 30, 0, tzinfo=timezone.utc).timestamp()
        assert format_msk_short(ts) == "06-15 12:30"


class TestFormatRelative:
    def test_just_now(self):
        assert format_relative(time.time()) == "just now"

    def test_minutes_ago(self):
        result = format_relative(time.time() - 5 * 60)
        assert "5 minute" in result and "ago" in result

    def test_one_minute_singular(self):
        result = format_relative(time.time() - 65)
        assert result == "1 minute ago"

    def test_one_hour(self):
        result = format_relative(time.time() - 3700)
        assert result == "an hour ago"

    def test_hours_ago(self):
        result = format_relative(time.time() - 5 * 3600)
        assert "5 hour" in result and "ago" in result

    def test_yesterday(self):
        result = format_relative(time.time() - 86400 - 100)
        assert result == "yesterday"

    def test_days_ago(self):
        result = format_relative(time.time() - 3 * 86400)
        assert result == "3 days ago"

    def test_negative_diff_clamped(self):
        # Future timestamp → "just now"
        result = format_relative(time.time() + 100)
        assert result == "just now"

    def test_long_ago_falls_through_to_date(self):
        # >7 days ago → форматируется как Mon DD
        ts = time.time() - 30 * 86400
        result = format_relative(ts)
        assert len(result) >= 3 and not result.endswith("ago")
