"""grep — безопасный поиск по рабочей директории без служебного мусора."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from config.constants import is_ignored_dir
from tools._paths import clean_path, resolve_path
from tools.models import ToolCall, ToolResult

_DEFAULT_MAX_RESULTS = 100
_MAX_RESULTS = 200


def _includes(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _visible_files(root: Path):
    if root.is_file():
        yield root, Path(root.name)
        return

    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") or is_ignored_dir(part) for part in relative.parts):
            continue
        if path.is_file():
            yield path, relative


def execute_grep(call: ToolCall) -> ToolResult:
    """Ищет regex в файлах либо перечисляет файлы по include-маскам."""
    args = call.args or {}
    path_arg = clean_path(args.get("path", "."))
    root = resolve_path(path_arg)
    command = f"grep {path_arg}"
    if not root.is_dir() and not root.is_file():
        return ToolResult(
            name="grep", status="error", exit_code=1, command=command,
            output=f"Search path is not a file or directory: {path_arg}",
        )

    pattern = args.get("pattern")
    if pattern is not None and not isinstance(pattern, str):
        pattern = str(pattern)
    include = _includes(args.get("include"))
    if not pattern and not include:
        return ToolResult(
            name="grep", status="error", exit_code=1, command=command,
            output="Provide pattern to search file contents or include to list matching files.",
        )

    try:
        regex = re.compile(pattern, 0 if args.get("case_sensitive") else re.IGNORECASE) if pattern else None
    except re.error as exc:
        return ToolResult(
            name="grep", status="error", exit_code=1, command=command,
            output=f"Invalid regular expression: {exc}",
        )

    limit = args.get("max_results", _DEFAULT_MAX_RESULTS)
    limit = max(1, min(int(limit), _MAX_RESULTS))
    results: list[str] = []
    matched_files = 0
    for file_path, relative in _visible_files(root):
        relative_text = relative.as_posix()
        if include and not any(fnmatch.fnmatch(relative_text, mask) for mask in include):
            continue
        if regex is None:
            results.append(relative_text)
            if len(results) >= limit:
                break
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_matched = False
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                results.append(f"{relative_text}:{number}:{line}")
                file_matched = True
                if len(results) >= limit:
                    break
        matched_files += file_matched
        if len(results) >= limit:
            break

    if not results:
        return ToolResult(
            name="grep", status="ok", exit_code=0, command=command,
            output="No matches found.",
        )
    summary = f"{len(results)} result(s)"
    if regex is not None:
        summary += f" in {matched_files} file(s)"
    if len(results) >= limit:
        summary += f" (limited to {limit})"
    return ToolResult(
        name="grep", status="ok", exit_code=0, command=command,
        output=f"{summary}:\n" + "\n".join(results),
    )
