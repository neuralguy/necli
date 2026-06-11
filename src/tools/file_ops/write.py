"""write_file и create_file — запись и создание файлов."""

import base64
import re

from logger import logger
from tools.models import ToolCall, ToolResult
from tools._paths import resolve_path, clean_path
from tools.file_checks import _run_ruff_on_python_file
from tools.file_ops.read import invalidate_read_cache

_resolve = resolve_path

_FENCE_LINE_RE = re.compile(r"^\s*(?::::call\b|call:::\s*$).*$", re.MULTILINE)


def _check_unbalanced_fences(content: str) -> str:
    """Возвращает warning если в content затесалась строка :::call/call:::

    Сами по себе :::call / call::: никогда не должны попасть в записываемый
    файл — это маркеры вызова инструмента, их наличие говорит о том, что
    парсер словил кусок чужого ответа в content.
    """
    if not content:
        return ""
    fences = _FENCE_LINE_RE.findall(content)
    if fences:
        return (
            "\n⚠ Suspicious :::call/call::: marker found in written content. "
            "This token is reserved for tool calls — your block boundaries are likely wrong."
        )
    return ""


def write_file(call: ToolCall) -> ToolResult:
    """Полностью перезаписывает файл. Content as-is, без обработки escape."""
    args = call.args
    path_str = clean_path(args.get("path", ""))
    if not path_str:
        return ToolResult(
            name="write_file",
            status="error",
            output="File path (path) not specified",
            exit_code=1,
            command=call.command,
        )

    path = _resolve(path_str)
    encoding = args.get("encoding", "utf-8")

    if "b64" in args:
        try:
            raw_b64 = args["b64"]
            if isinstance(raw_b64, str):
                raw_b64 = raw_b64.replace("\n", "").replace("\r", "").replace(" ", "")
            content = base64.b64decode(raw_b64).decode(encoding)
        except Exception as e:
            return ToolResult(
                name="write_file",
                status="error",
                output=f"base64 decoding error: {e}",
                exit_code=1,
                command=call.command,
            )
    elif "content" in args:
        content = args["content"]
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
    else:
        return ToolResult(
            name="write_file",
            status="error",
            output="Content (content or b64) not specified",
            exit_code=1,
            command=call.command,
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        existed = path.exists()
        old_size = path.stat().st_size if existed else 0

        path.write_text(content, encoding=encoding)
        invalidate_read_cache(path)

        new_size = path.stat().st_size
        lines = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )

        logger.info(
            "write_file: {} ({}, {}→{}b, {} lines)",
            path_str, "overwrite" if existed else "create", old_size, new_size, lines,
        )

        action = "overwritten" if existed else "created"
        msg = f"✓ {path_str}: {action}, {lines} lines"
        msg += _check_unbalanced_fences(content)
        msg += _run_ruff_on_python_file(path, path_str)

        return ToolResult(
            name="write_file",
            status="ok",
            output=msg,
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        logger.opt(exception=True).error("write_file failed for {}: {}", path_str, e)
        return ToolResult(
            name="write_file",
            status="error",
            output=f"Write error: {e}",
            exit_code=1,
            command=call.command,
        )


def create_file(call: ToolCall) -> ToolResult:
    """Создаёт новый файл; ошибка если уже существует."""
    args = call.args
    path_str = clean_path(args.get("path", ""))
    if not path_str:
        return ToolResult(
            name="create_file",
            status="error",
            output="Path (path) not specified",
            exit_code=1,
            command=call.command,
        )

    path = _resolve(path_str)

    if path.exists():
        return ToolResult(
            name="create_file",
            status="error",
            output=(
                f"File already exists: {path_str}. "
                f"Use write_file to overwrite or patch_file to modify."
            ),
            exit_code=1,
            command=call.command,
        )

    content = args.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        invalidate_read_cache(path)
        size = path.stat().st_size
        lines = content.count("\n") + (
            1 if content and not content.endswith("\n") else 0
        )
        logger.info("create_file: {} ({}b, {} lines)", path_str, size, lines)
        msg = f"✓ Created: {path_str} ({lines} lines)"
        msg += _check_unbalanced_fences(content)
        msg += _run_ruff_on_python_file(path, path_str)

        return ToolResult(
            name="create_file",
            status="ok",
            output=msg,
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        logger.opt(exception=True).error("create_file failed for {}: {}", path_str, e)
        return ToolResult(
            name="create_file",
            status="error",
            output=f"Creation error: {e}",
            exit_code=1,
            command=call.command,
        )