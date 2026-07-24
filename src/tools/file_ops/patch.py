"""patch_file — точечное редактирование файлов: patches/find-replace/insert/delete."""

import difflib

from logger import logger
from tools._paths import clean_path, resolve_path
from tools.file_ops._fuzzy import _fuzzy_find_replace
from tools.file_ops.read import invalidate_read_cache
from tools.models import ToolCall, ToolResult

_resolve = resolve_path


def _reveal_ws(line: str) -> str:
    """Делает невидимые символы видимыми: таб→»·, trailing-пробелы→·, CR→<CR>.

    Чистый find/replace падает на расхождении в пробелах/табах, а модель видит
    «вроде совпадает» и бьётся. Показываем точные невидимые символы, чтобы она
    исправила find с первого раза, а не гадала.
    """
    line = line.replace("\r", "↵")
    # trailing whitespace (пробелы/табы в конце) помечаем точками/стрелками
    stripped = line.rstrip(" \t")
    trailing = line[len(stripped):]
    body = stripped.replace("\t", "»   ")
    trailing = trailing.replace("\t", "»   ").replace(" ", "·")
    return body + trailing


def patch_file(call: ToolCall) -> ToolResult:
    """Точечное редактирование: patches | find/replace | line/insert | delete_lines."""
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
    changes = []
    line_starts: list[int] = []

    def _line_of(text: str, needle: str) -> int:
        idx = text.find(needle)
        if idx < 0:
            return 1
        return text.count("\n", 0, idx) + 1

    if "patches" in args:
        return ToolResult(
            name="patch_file",
            status="error",
            output=(
                "Multiple patches in one call are not allowed. patch_file does "
                "ONE change per call (find/replace, line/insert, or delete_lines). "
                "Make a separate patch_file call for each edit."
            ),
            exit_code=1,
            command=call.command,
        )

    elif "find" in args:
        find = args["find"]
        replace = args.get("replace", "")
        if find not in modified:
            modified, found_fuzzy = _fuzzy_find_replace(modified, find, replace)
            if found_fuzzy:
                changes.append("  find/replace (fuzzy): applied 1")
            else:
                hint = ""
                try:
                    find_lines = find.splitlines()
                    if find_lines:
                        file_lines = modified.splitlines()
                        matcher = difflib.get_close_matches(find_lines[0], file_lines, n=1, cutoff=0.6)
                        if matcher:
                            idx = file_lines.index(matcher[0])
                            ctx_start = max(0, idx - 1)
                            ctx_end = min(len(file_lines), idx + len(find_lines) + 1)
                            ctx = "\n".join(f"  {i+1}: {_reveal_ws(ln)}" for i, ln in enumerate(file_lines[ctx_start:ctx_end], start=ctx_start))
                            # Показываем твою find-строку и ближайшую в файле с
                            # видимыми пробелами/табами — частая причина промаха.
                            ws_legend = "(whitespace shown: »=tab, ·=trailing space, ↵=CR)"
                            cmp_block = (
                                f"\n\nYour find (line 1):\n  {_reveal_ws(find_lines[0])}"
                                f"\nClosest in file (line {idx+1}):\n  {_reveal_ws(matcher[0])}"
                            )
                            hint = (
                                f"\n\nClosest match in file (around line {idx+1}) {ws_legend}:\n{ctx}"
                                f"{cmp_block}"
                                "\n\nNote: if you ran multiple patch_file calls in one response, an earlier patch may have modified this fragment. Re-read the file."
                            )
                except Exception:
                    logger.debug("patch_file: close-match hint failed for {}", path_str, exc_info=True)
                return ToolResult(
                    name="patch_file",
                    status="error",
                    output=f"Fragment not found in {path_str}:\n'{find[:200]}'{hint}",
                    exit_code=1,
                    command=call.command,
                )
        else:
            line_starts.append(_line_of(modified, find))
            modified = modified.replace(find, replace, 1)
            changes.append("  find/replace: applied 1")

    else:
        return ToolResult(
            name="patch_file",
            status="error",
            output=("Specify find and replace"),
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
        path.write_text(modified, encoding="utf-8")
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
        path_str, added, removed, changed, len(changes),
    )

    return ToolResult(
        name="patch_file",
        status="ok",
        output="\n".join(output_parts),
        exit_code=0,
        command=call.command,
        line_starts=line_starts or None,
    )
