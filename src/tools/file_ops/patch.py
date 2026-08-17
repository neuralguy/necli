"""patch_file — точечное редактирование файлов: patches/find-replace/insert/delete."""

import difflib
import re

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


def _detect_eol(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\n"


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


def _parse_delete_lines(value, total: int) -> tuple[set[int] | None, str | None]:
    """Parse 1-based `3`, `3-5`, or comma-separated ranges."""
    if isinstance(value, int):
        raw = str(value)
    else:
        raw = str(value or "").strip()
    if not raw:
        return None, "delete_lines is empty"

    selected: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            return None, f"Invalid delete_lines range: {value!r}. Use e.g. 3, 3-5, or 2,5-7."
        start = int(m.group(1))
        end = int(m.group(2) or start)
        if start > end:
            start, end = end, start
        if start < 1 or end > total:
            return None, f"delete_lines {start}-{end} is outside file range 1-{total}."
        selected.update(range(start, end + 1))
    return selected, None


def _run_fuzzy(text: str, find: str, replace: str) -> tuple[str, bool]:
    """Compatibility wrapper: tests/plugins may monkeypatch old 2-tuple implementation."""
    result = _fuzzy_find_replace(text, find, replace)
    if isinstance(result, tuple) and len(result) >= 2:
        return result[0], bool(result[1])
    return text, False


def patch_file(call: ToolCall) -> ToolResult:
    """Atomic targeted edit: patches | find/replace | line/insert | delete_lines."""
    args = call.args
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

    if args.get("patches"):
        patches = args["patches"]
        if not isinstance(patches, list):
            return ToolResult(
                name="patch_file",
                status="error",
                output="'patches' must be a list of {find, replace} objects.",
                exit_code=1,
                command=call.command,
            )
        for i, patch in enumerate(patches):
            if not isinstance(patch, dict) or "find" not in patch:
                return ToolResult(
                    name="patch_file",
                    status="error",
                    output=f"patches[{i}]: each patch must have 'find' key. No changes were written.",
                    exit_code=1,
                    command=call.command,
                )
            find = str(patch["find"])
            replace = str(patch.get("replace", ""))
            if not find:
                return ToolResult(
                    name="patch_file",
                    status="error",
                    output=f"patches[{i}]: empty 'find' string. No changes were written.",
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

    elif "find" in args:
        find = str(args["find"])
        replace = str(args.get("replace", ""))
        if not find:
            return ToolResult(
                name="patch_file",
                status="error",
                output="Empty 'find' string — nothing to search for. No changes were written.",
                exit_code=1,
                command=call.command,
            )
        if find in modified:
            line_starts.append(_line_of(modified, find))
            modified = modified.replace(find, replace, 1)
            changes.append("  find/replace: applied 1")
        else:
            fuzzy_modified, found_fuzzy = _run_fuzzy(modified, find, replace)
            if not found_fuzzy:
                return ToolResult(
                    name="patch_file",
                    status="error",
                    output=_not_found_output(path_str, find, modified),
                    exit_code=1,
                    command=call.command,
                )
            modified = fuzzy_modified
            changes.append("  find/replace (fuzzy): applied 1")

    elif "insert" in args:
        try:
            line = int(args.get("line"))
        except (TypeError, ValueError):
            return ToolResult(
                name="patch_file",
                status="error",
                output="INSERT requires a 1-based integer 'line'. No changes were written.",
                exit_code=1,
                command=call.command,
            )
        source_lines = modified.splitlines(keepends=True)
        if line < 1 or line > len(source_lines) + 1:
            return ToolResult(
                name="patch_file",
                status="error",
                output=f"INSERT line {line} is outside valid range 1-{len(source_lines) + 1}. No changes were written.",
                exit_code=1,
                command=call.command,
            )
        idx = line - 1
        eol = _detect_eol(modified)
        prefix = "".join(source_lines[:idx])
        suffix = "".join(source_lines[idx:])
        insert = str(args.get("insert", ""))
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += eol
        if insert and suffix and not insert.endswith(("\n", "\r")):
            insert += eol
        elif insert and not suffix and original.endswith(("\n", "\r")) and not insert.endswith(("\n", "\r")):
            insert += eol
        modified = prefix + insert + suffix
        line_starts.append(line)
        changes.append(f"  insert @ line {line}: applied")

    elif "delete_lines" in args:
        source_lines = modified.splitlines(keepends=True)
        selected, error = _parse_delete_lines(args.get("delete_lines"), len(source_lines))
        if error:
            return ToolResult(
                name="patch_file",
                status="error",
                output=f"{error} No changes were written.",
                exit_code=1,
                command=call.command,
            )
        assert selected is not None
        first = min(selected)
        last = max(selected)
        modified = "".join(line for i, line in enumerate(source_lines, start=1) if i not in selected)
        line_starts.append(first)
        changes.append(f"  delete lines {first}-{last}: applied")

    else:
        return ToolResult(
            name="patch_file",
            status="error",
            output="Specify patches, find/replace, insert+line, or delete_lines.",
            exit_code=1,
            command=call.command,
        )

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
