"""patch_file — atomic targeted edits through a canonical patches list."""

import difflib

from config._atomic import atomic_write_text
from logger import logger
from tools._paths import clean_path, resolve_path
from tools.file_ops._fuzzy import _fuzzy_find_replace
from tools.file_ops.read import invalidate_read_cache
from tools.models import ToolCall, ToolResult

_resolve = resolve_path


def _reveal_ws(line: str) -> str:
    """Делает невидимые символы видимыми: таб→»·, trailing-пробелы→·, CR→<CR>."""
    line = line.replace("\r", "↵")
    stripped = line.rstrip(" \t")
    trailing = line[len(stripped) :]
    body = stripped.replace("\t", "»   ")
    trailing = trailing.replace("\t", "»   ").replace(" ", "·")
    return body + trailing


def _line_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    if idx < 0:
        return 1
    return text.count("\n", 0, idx) + 1


def _build_patch_changes(original: str, modified: str) -> list[dict]:
    """Actual line hunks used by the renderer instead of reconstructing from args."""
    old_lines = original.splitlines()
    new_lines = modified.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    changes: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            {
                "old_start": i1 + 1,
                "new_start": j1 + 1,
                "old_lines": old_lines[i1:i2],
                "new_lines": new_lines[j1:j2],
            }
        )
    return changes


def _close_match_hint(text: str, find: str) -> str:
    """Actionable nearest-match diagnostics for a failed patch."""
    try:
        find_lines = find.splitlines()
        if not find_lines:
            return ""
        file_lines = text.splitlines()
        matcher = difflib.get_close_matches(find_lines[0], file_lines, n=1, cutoff=0.55)
        if not matcher:
            return ""
        idx = file_lines.index(matcher[0])
        ctx_start = max(0, idx - 1)
        ctx_end = min(len(file_lines), idx + max(1, len(find_lines)) + 1)
        ctx = "\n".join(
            f"  {i + 1}: {_reveal_ws(ln)}"
            for i, ln in enumerate(file_lines[ctx_start:ctx_end], start=ctx_start)
        )
        legend = "(whitespace shown: »=tab, ·=trailing space, ↵=CR)"
        return (
            f"\n\nClosest match in the current file (around line {idx + 1}) {legend}:\n{ctx}"
            f"\n\nYour FIND starts with:\n  {_reveal_ws(find_lines[0])}"
            f"\nClosest file line:\n  {_reveal_ws(matcher[0])}"
        )
    except Exception:
        logger.debug("patch_file: close-match hint failed", exc_info=True)
        return ""


def _not_found_output(path_str: str, find: str, current_text: str, *, patch_index: int | None = None) -> str:
    where = f"patches[{patch_index}]" if patch_index is not None else "FIND"
    preview = find[:400]
    return (
        f"PATCH FAILED — no changes were written to {path_str}.\n"
        f"{where} could not be matched in the current file:\n{preview!r}"
        f"{_close_match_hint(current_text, find)}"
        "\n\nRe-read the affected lines and retry with a smaller, unique FIND block copied from the current file. "
        "Do not assume this patch applied. A multi-patch call is atomic: if any pair fails, earlier pairs "
        "from the same call are rolled back too."
    )


def _run_fuzzy(text: str, find: str, replace: str) -> tuple[str, bool]:
    """Compatibility wrapper: tests/plugins may monkeypatch old 2-tuple implementation."""
    result = _fuzzy_find_replace(text, find, replace)
    if isinstance(result, tuple) and len(result) >= 2:
        return result[0], bool(result[1])
    return text, False


def patch_file(call: ToolCall) -> ToolResult:
    """Atomic targeted edit through one canonical `patches` list."""
    args = call.args
    unexpected = sorted(k for k in args if k not in {"path", "patches"})
    if unexpected:
        return ToolResult(
            name="patch_file",
            status="error",
            output=(
                "Unexpected patch_file parameter(s): "
                f"{', '.join(unexpected)}. Use only path and patches."
            ),
            exit_code=1,
            command=call.command,
        )
    path_str = clean_path(args.get("path", ""))
    if not path_str:
        return ToolResult(
            name="patch_file",
            status="error",
            output="File path (path) not specified",
            exit_code=1,
            command=call.command,
        )

    path = _resolve(path_str)
    if not path.exists():
        return ToolResult(
            name="patch_file",
            status="error",
            output=f"File not found: {path}",
            exit_code=1,
            command=call.command,
        )

    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ToolResult(
            name="patch_file",
            status="error",
            output=f"Read error: {e}",
            exit_code=1,
            command=call.command,
        )

    modified = original
    changes: list[str] = []
    line_starts: list[int] = []

    patches = args.get("patches")
    if not isinstance(patches, list) or not patches:
        return ToolResult(
            name="patch_file",
            status="error",
            output="Specify a non-empty patches list of {find, replace} objects.",
            exit_code=1,
            command=call.command,
        )

    for i, patch in enumerate(patches):
        if not isinstance(patch, dict) or "find" not in patch or "replace" not in patch:
            return ToolResult(
                name="patch_file",
                status="error",
                output=(
                    f"patches[{i}]: each patch must contain exactly 'find' and 'replace'. "
                    "No changes were written."
                ),
                exit_code=1,
                command=call.command,
            )
        if set(patch) != {"find", "replace"}:
            return ToolResult(
                name="patch_file",
                status="error",
                output=(
                    f"patches[{i}]: unexpected keys; use only 'find' and 'replace'. "
                    "No changes were written."
                ),
                exit_code=1,
                command=call.command,
            )
        find = patch["find"]
        replace = patch["replace"]
        if not isinstance(find, str) or not find:
            return ToolResult(
                name="patch_file",
                status="error",
                output=f"patches[{i}]: 'find' must be a non-empty string. No changes were written.",
                exit_code=1,
                command=call.command,
            )
        if not isinstance(replace, str):
            return ToolResult(
                name="patch_file",
                status="error",
                output=f"patches[{i}]: 'replace' must be a string. No changes were written.",
                exit_code=1,
                command=call.command,
            )
        if find in modified:
            line_starts.append(_line_of(modified, find))
            modified = modified.replace(find, replace, 1)
            changes.append(f"  patches[{i}] find/replace: applied 1")
            continue

        fuzzy_modified, found = _run_fuzzy(modified, find, replace)
        if not found:
            return ToolResult(
                name="patch_file",
                status="error",
                output=_not_found_output(path_str, find, modified, patch_index=i),
                exit_code=1,
                command=call.command,
            )
        modified = fuzzy_modified
        changes.append(f"  patches[{i}] find/replace (fuzzy): applied 1")

    if modified == original:
        return ToolResult(
            name="patch_file",
            status="ok",
            output=f"No changes in {path_str}",
            exit_code=0,
            command=call.command,
        )

    try:
        atomic_write_text(path, modified, encoding="utf-8")
        invalidate_read_cache(path)
    except Exception as e:
        logger.opt(exception=True).error("patch_file write failed for {}: {}", path_str, e)
        return ToolResult(
            name="patch_file",
            status="error",
            output=f"Write error: {e}",
            exit_code=1,
            command=call.command,
        )

    diff = difflib.ndiff(original.splitlines(), modified.splitlines())
    added = 0
    removed = 0
    for line in diff:
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    changed = min(added, removed)
    added -= changed
    removed -= changed

    stats_parts = []
    if changed:
        stats_parts.append(f"{changed} changed")
    if added:
        stats_parts.append(f"+{added} added")
    if removed:
        stats_parts.append(f"-{removed} removed")
    stats = ", ".join(stats_parts) if stats_parts else "changed"

    output_parts = [f"✓ {path_str} updated ({stats})"]
    output_parts.extend(changes)

    logger.info(
        "patch_file: {} (+{} -{} ~{}, sections={})",
        path_str,
        added,
        removed,
        changed,
        len(changes),
    )

    return ToolResult(
        name="patch_file",
        status="ok",
        output="\n".join(output_parts),
        exit_code=0,
        command=call.command,
        line_starts=line_starts or None,
        patch_changes=_build_patch_changes(original, modified) or None,
    )
