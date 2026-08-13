"""Normalized submission types exchanged between UI event sources and the command loop."""

from __future__ import annotations

SUBMIT_USER = "user"
SUBMIT_SLASH = "slash"
SUBMIT_EOF = "eof"
SUBMIT_INTERRUPT = "interrupt"
SUBMIT_BG_RESUME = "bg_resume"
SUBMIT_TG = "tg"

__all__ = (
    "SUBMIT_BG_RESUME",
    "SUBMIT_EOF",
    "SUBMIT_INTERRUPT",
    "SUBMIT_SLASH",
    "SUBMIT_TG",
    "SUBMIT_USER",
)
