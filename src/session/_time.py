"""Единое форматирование timestamps по МСК."""

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def format_msk(ts: float) -> str:
    """Unix timestamp → строка по МСК (полный формат)."""
    return datetime.fromtimestamp(ts, tz=MSK).strftime("%Y-%m-%d %H:%M:%S")


def format_msk_short(ts: float) -> str:
    """Unix timestamp → строка по МСК (короткий формат)."""
    return datetime.fromtimestamp(ts, tz=MSK).strftime("%m-%d %H:%M")


def format_relative(ts: float) -> str:
    """Unix timestamp → относительное время ('2 minutes ago', 'Sep 03')."""
    import time
    now = time.time()
    diff = now - ts
    if diff < 0:
        diff = 0

    if diff < 60:
        return "just now"
    if diff < 3600:
        m = int(diff // 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if diff < 86400:
        h = int(diff // 3600)
        if h == 1:
            return "an hour ago"
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if diff < 86400 * 7:
        d = int(diff // 86400)
        if d == 1:
            return "yesterday"
        return f"{d} days ago"
    dt = datetime.fromtimestamp(ts, tz=MSK)
    now_dt = datetime.fromtimestamp(now, tz=MSK)
    if dt.year == now_dt.year:
        return dt.strftime("%b %d")
    return dt.strftime("%b %d %Y")
