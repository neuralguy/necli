from __future__ import annotations

import re

from loguru import logger

from logger import LOGS_DIR

_LOG_PATH = LOGS_DIR / "opus48_transcript_debug.log"

# Matches the kinds of transcript blocks we saw leaked into assistant text.
_TRANSCRIPT_HINT_RE = re.compile(
    r"(?m)^(?:\s*●\s*)?(?:user|assistant)?\$\s+.+$"
    r"|^\s*\[Project:\s+\d+\s+files,\s+\d+[\d,]*\s+lines.*\]$"
    r"|^\s*\(plus\s+\d+\s+more\s+tool\s+results\)\s*$",
    re.IGNORECASE,
)


def _shorten(s: str, limit: int = 2000) -> str:
    if s is None:
        return ""
    if len(s) <= limit:
        return s
    return s[: limit // 2] + "\n…(truncated)…\n" + s[-limit // 2 :]


def has_transcript_hint(text: str) -> bool:
    if not text:
        return False
    return bool(_TRANSCRIPT_HINT_RE.search(text))


def log_event(event: str, **fields) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        parts = [f"[{event}]"]
        for k, v in fields.items():
            if v is None:
                continue
            if isinstance(v, str):
                v = _shorten(v)
            parts.append(f"{k}={v!r}")
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(" ".join(parts) + "\n")
    except Exception as e:
        logger.warning("opus48 debug log write failed: {}: {}", type(e).__name__, e)