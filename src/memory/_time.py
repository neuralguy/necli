"""Timestamp helpers used by memory producers."""

from __future__ import annotations

from datetime import datetime


def current_timestamp() -> str:
    """Return an ISO-8601 local timestamp with second precision."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
