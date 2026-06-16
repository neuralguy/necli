"""Session notes for long-running autonomous work."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_MAX_NOTE_CHARS = 12000
_MAX_MESSAGE_CHARS = 1200

_TEMPLATE = """# Session Title
_A short and distinctive 5-10 word descriptive title for the session._

# Current State
_What is actively being worked on right now? Pending tasks and immediate next steps._

# Task specification
_What did the user ask to build or investigate? Important constraints and decisions._

# Files and Functions
_Important files/functions touched or discovered, and why they matter._

# Workflow
_Commands/checks usually run and how to interpret them._

# Errors & Corrections
_Errors encountered, user corrections, failed approaches to avoid._

# Verification
_Checks run, verifier verdicts, remaining gaps._

# Worklog
_Step-by-step terse worklog._
"""


def notes_path(session_dir: str | Path) -> Path:
    return Path(session_dir) / "session_notes.md"


def ensure_session_notes(session_dir: str | Path) -> Path:
    path = notes_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_TEMPLATE, encoding="utf-8")
    return path


def format_session_notes_block(session_dir: str | Path | None) -> str:
    if not session_dir:
        return ""
    path = notes_path(session_dir)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text or text.strip() == _TEMPLATE.strip():
        return ""
    if len(text) > _MAX_NOTE_CHARS:
        text = text[:6000] + "\n\n[... session notes truncated ...]\n\n" + text[-5000:]
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "SESSION NOTES (continuity for long tasks)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + text
    )


def save_session_notes(session: Any) -> None:
    path = ensure_session_notes(session.dir)
    messages = list(getattr(session, "messages", []) or [])
    user_messages = [m for m in messages if getattr(m, "role", "") == "user"]
    assistant_messages = [m for m in messages if getattr(m, "role", "") == "assistant"]
    tool_messages = [m for m in messages if getattr(m, "role", "") == "tool_result"]

    title = getattr(session, "title", "") or (user_messages[0].content[:80] if user_messages else "Untitled session")
    current = _trim(assistant_messages[-1].content if assistant_messages else "")
    task_spec = _trim(user_messages[-1].content if user_messages else "")
    worklog = []
    for msg in messages[-12:]:
        role = getattr(msg, "role", "")
        if role not in ("user", "assistant", "tool_result"):
            continue
        content = _trim(getattr(msg, "content", ""), 500)
        if content:
            worklog.append(f"- {role}: {content}")

    verification = []
    for msg in tool_messages[-6:]:
        content = getattr(msg, "content", "") or ""
        upper = content.upper()
        if "VERDICT:" in upper or "PYTEST" in upper or "LSP_DIAGNOSTICS" in upper or "ERROR" in upper:
            verification.append(f"- {_trim(content, 700)}")

    files = _extract_file_mentions("\n".join(getattr(m, "content", "") or "" for m in messages[-20:]))
    text = f"""# Session Title
_A short and distinctive 5-10 word descriptive title for the session._
{_trim(title, 180)}

# Current State
_What is actively being worked on right now? Pending tasks and immediate next steps._
{current or "(no assistant work recorded yet)"}

# Task specification
_What did the user ask to build or investigate? Important constraints and decisions._
{task_spec or "(no user request recorded yet)"}

# Files and Functions
_Important files/functions touched or discovered, and why they matter._
{chr(10).join(f"- `{f}`" for f in files[:40]) or "(none captured yet)"}

# Workflow
_Commands/checks usually run and how to interpret them._
Use project-local commands via `uv`. Prefer targeted `python3 -c` checks for new behavior and run the relevant test subset before final reporting.

# Errors & Corrections
_Errors encountered, user corrections, failed approaches to avoid._
{_collect_errors(messages)}

# Verification
_Checks run, verifier verdicts, remaining gaps._
{chr(10).join(verification) or "(no verification captured yet)"}

# Worklog
_Step-by-step terse worklog._
{chr(10).join(worklog) or "(empty)"}
"""
    path.write_text(text[-_MAX_NOTE_CHARS:], encoding="utf-8")


def _trim(text: str, limit: int = _MAX_MESSAGE_CHARS) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit // 2].rstrip() + " … " + text[-limit // 2 :].lstrip()


def _extract_file_mentions(text: str) -> list[str]:
    import re

    seen = set()
    out = []
    for match in re.findall(r"\b(?:src|tests|docs|scripts)/[A-Za-z0-9_./-]+\.[A-Za-z0-9_]+", text):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out


def _collect_errors(messages: list[Any]) -> str:
    rows = []
    for msg in messages[-20:]:
        content = getattr(msg, "content", "") or ""
        lower = content.lower()
        if "error" in lower or "failed" in lower or "traceback" in lower:
            rows.append(f"- {_trim(content, 700)}")
    return "\n".join(rows) or "(none captured yet)"