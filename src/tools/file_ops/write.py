"""create_file — создание и перезапись файлов."""

import base64
import re

from logger import logger
from tools._paths import clean_path, resolve_path
from tools.file_ops.read import invalidate_read_cache
from tools.models import ToolCall, ToolResult

_resolve = resolve_path

_FENCE_LINE_RE = re.compile(r"^\s*(?::{2,3}call\b|call:{2,3}\s*$).*$", re.MULTILINE)


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


def create_file(call: ToolCall) -> ToolResult:
    """Создаёт или ПОЛНОСТЬЮ перезаписывает файл. Content as-is, без escape.

    Единственный инструмент записи файла целиком (бывший write_file удалён):
    создаёт новый файл или перезаписывает существующий. Для точечных правок —
    patch_file.
    """
    args = call.args
    path_str = clean_path(args.get("path", ""))
    if not path_str:
        return ToolResult(
            name="create_file",
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
                name="create_file",
                status="error",
                output=f"base64 decoding error: {e}",
                exit_code=1,
                command=call.command,
            )
    else:
        content = args.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)

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
            "create_file: {} ({}, {}→{}b, {} lines)",
            path_str, "overwrite" if existed else "create", old_size, new_size, lines,
        )

        action = "Overwritten" if existed else "Created"
        msg = f"✓ {action}: {path_str} ({lines} lines)"
        msg += _check_unbalanced_fences(content)
        if path.suffix == ".py":
            from tools.auto_checks import queue_python_auto_check
            if queue_python_auto_check(path, path_str):
                msg += "\n↻ auto-check queued: lsp_diagnostics + ruff"

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
            output=f"Write error: {e}",
            exit_code=1,
            command=call.command,
        )
