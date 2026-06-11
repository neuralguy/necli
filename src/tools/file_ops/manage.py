"""Управление файлами: delete_file, rename_file, copy_file, move_file."""

import shutil

from logger import logger
from tools.models import ToolCall, ToolResult
from tools._paths import resolve_path, clean_path
from tools.file_ops.read import invalidate_read_cache

_resolve = resolve_path

def delete_file(call: ToolCall) -> ToolResult:
    """Delete a file."""
    args = call.args
    path_str = clean_path(args.get("path", args.get("raw", "")))
    if not path_str:
        return ToolResult(
            name="delete_file",
            status="error",
            output="Path (path) not specified",
            exit_code=1,
            command=call.command,
        )

    path = _resolve(path_str)

    if not path.exists():
        return ToolResult(
            name="delete_file",
            status="error",
            output=f"File not found: {path_str}",
            exit_code=1,
            command=call.command,
        )

    if not path.is_file():
        return ToolResult(
            name="delete_file",
            status="error",
            output=f"Not a file: {path_str} (use rmdir for directories)",
            exit_code=1,
            command=call.command,
        )

    try:
        invalidate_read_cache(path)
        if path.suffix.lower() == ".docx":
            try:
                from tools.file_ops._docx_sources import delete_source
                delete_source(path)
            except Exception:
                logger.debug("docx source delete skipped", exc_info=True)
        path.unlink()
        return ToolResult(
            name="delete_file",
            status="ok",
            output=f"✓ Deleted: {path_str}",
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        logger.opt(exception=True).error("delete_file failed for {}: {}", path_str, e)
        return ToolResult(
            name="delete_file",
            status="error",
            output=f"Deletion error: {e}",
            exit_code=1,
            command=call.command,
        )

def rename_file(call: ToolCall) -> ToolResult:
    """Rename or move a file."""
    args = call.args
    path_str = clean_path(args.get("path", args.get("source", args.get("src", ""))))
    new_path_str = clean_path(args.get("new_path", args.get("dest", args.get("destination", args.get("dst", "")))))

    if not path_str or not new_path_str:
        return ToolResult(
            name="rename_file",
            status="error",
            output="Specify path and new_path",
            exit_code=1,
            command=call.command,
        )

    src = _resolve(path_str)
    dst = _resolve(new_path_str)

    if not src.exists():
        return ToolResult(
            name="rename_file",
            status="error",
            output=f"Not found: {path_str}",
            exit_code=1,
            command=call.command,
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        invalidate_read_cache(src)
        invalidate_read_cache(dst)
        # shutil.move корректно работает между файловыми системами
        # (Path.rename падает с OSError: Invalid cross-device link).
        shutil.move(str(src), str(dst))
        return ToolResult(
            name="rename_file",
            status="ok",
            output=f"✓ {path_str} → {new_path_str}",
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        logger.opt(exception=True).error("rename_file failed for {} → {}: {}", path_str, new_path_str, e)
        return ToolResult(
            name="rename_file",
            status="error",
            output=f"Error: {e}",
            exit_code=1,
            command=call.command,
        )

def copy_file(call: ToolCall) -> ToolResult:
    """Copy a file or directory."""
    args = call.args
    path_str = clean_path(args.get("path", args.get("source", args.get("src", ""))))
    dest_str = clean_path(args.get("dest", args.get("destination", args.get("dst", ""))))

    if not path_str or not dest_str:
        return ToolResult(
            name="copy_file",
            status="error",
            output="Specify path and dest",
            exit_code=1,
            command=call.command,
        )

    src = _resolve(path_str)
    dst = _resolve(dest_str)

    if not src.exists():
        return ToolResult(
            name="copy_file",
            status="error",
            output=f"Not found: {path_str}",
            exit_code=1,
            command=call.command,
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        invalidate_read_cache(dst)
        return ToolResult(
            name="copy_file",
            status="ok",
            output=f"✓ Copied: {path_str} → {dest_str}",
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        logger.opt(exception=True).error("copy_file failed for {} → {}: {}", path_str, dest_str, e)
        return ToolResult(
            name="copy_file",
            status="error",
            output=f"Error: {e}",
            exit_code=1,
            command=call.command,
        )

def move_file(call: ToolCall) -> ToolResult:
    """Move a file or directory."""
    args = call.args
    path_str = clean_path(args.get("path", args.get("source", args.get("src", ""))))
    dest_str = clean_path(args.get("dest", args.get("destination", args.get("dst", ""))))

    if not path_str or not dest_str:
        return ToolResult(
            name="move_file",
            status="error",
            output="Specify path and dest",
            exit_code=1,
            command=call.command,
        )

    src = _resolve(path_str)
    dst = _resolve(dest_str)

    if not src.exists():
        return ToolResult(
            name="move_file",
            status="error",
            output=f"Not found: {path_str}",
            exit_code=1,
            command=call.command,
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        invalidate_read_cache(src)
        invalidate_read_cache(dst)
        shutil.move(str(src), str(dst))
        return ToolResult(
            name="move_file",
            status="ok",
            output=f"✓ Moved: {path_str} → {dest_str}",
            exit_code=0,
            command=call.command,
        )
    except Exception as e:
        logger.opt(exception=True).error("move_file failed for {} → {}: {}", path_str, dest_str, e)
        return ToolResult(
            name="move_file",
            status="error",
            output=f"Error: {e}",
            exit_code=1,
            command=call.command,
        )