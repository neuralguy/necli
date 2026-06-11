"""
Parsing @-references in user messages and injecting file/folder content.

Supports:
  @path/to/file.py  - injects file content
  @path/to/folder/  - injects tree + all files in folder
"""

import logging
import os
import re
from pathlib import Path

from ui._filters import should_ignore as _should_ignore

logger = logging.getLogger(__name__)


# Max file size to inline (100KB)
_MAX_FILE_SIZE = 100 * 1024
# Max files when expanding a directory
_MAX_DIR_FILES = 30
# Max total context size (300KB)
_MAX_TOTAL_SIZE = 300 * 1024


# Regex to find @references in text
# @ at start of string or after whitespace, followed by a path-like string
# Also matches special keyword @all
_AT_REF_RE = re.compile(
    r"(?:^|(?<=\s))@(all|(?:[a-zA-Z0-9_.][a-zA-Z0-9_./-]*[a-zA-Z0-9_./])|(?:[a-zA-Z0-9_.]))"
)


def _read_file_content(path, max_size=_MAX_FILE_SIZE):
    """Read file content, return (content, was_truncated)."""
    try:
        size = path.stat().st_size
        if size > max_size:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_size)
            return content + f"\n... [truncated, {size} bytes total]", True
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), False
    except (OSError, UnicodeDecodeError):
        return None, False


def _collect_dir_files(dir_path, max_files=_MAX_DIR_FILES):
    """Collect readable files from directory recursively."""
    files = []
    try:
        for root, dirs, filenames in os.walk(dir_path):
            # Filter ignored dirs in-place
            dirs[:] = [
                d for d in sorted(dirs)
                if not _should_ignore(d, True)
            ]
            for fname in sorted(filenames):
                if _should_ignore(fname, False):
                    continue
                fpath = Path(root) / fname
                try:
                    rel = fpath.relative_to(dir_path)
                except ValueError:
                    continue
                files.append((str(rel), fpath))
                if len(files) >= max_files:
                    return files
    except (PermissionError, OSError):
        pass
    return files


def _build_tree(dir_path, max_depth=3):
    """Build a simple tree string for a directory."""
    result = []
    
    def _walk(path, prefix, depth):
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except (PermissionError, OSError):
            return
        filtered = [e for e in entries if not _should_ignore(e.name, e.is_dir())]
        for i, entry in enumerate(filtered):
            is_last = (i == len(filtered) - 1)
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                result.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)
            else:
                result.append(f"{prefix}{connector}{entry.name}")
    
    _walk(dir_path, "", 0)
    return "\n".join(result)


class FileReference:
    """A parsed @-reference."""
    
    def __init__(self, raw, path_str, resolved_path, is_dir=False):
        self.raw = raw          # "@src/main.py"
        self.path_str = path_str  # "src/main.py"
        self.resolved_path = resolved_path
        self.is_dir = is_dir
        self.content = None
        self.error = None


def parse_at_references(text, working_dir):
    """
    Find all @-references in text and return list of FileReference.
    """
    refs = []
    seen_paths = set()
    
    for m in _AT_REF_RE.finditer(text):
        path_str = m.group(1)
        # Skip obvious non-paths
        if path_str in ("tool", "plan", "param"):
            continue

        # @all — весь текущий проект (working_dir как директория)
        if path_str == "all":
            resolved = Path(working_dir).resolve()
            if str(resolved) in seen_paths:
                continue
            seen_paths.add(str(resolved))
            ref = FileReference("@all", ".", resolved, is_dir=True)
            refs.append(ref)
            continue

        resolved = Path(working_dir) / path_str
        resolved = resolved.resolve()
        
        if str(resolved) in seen_paths:
            continue
        seen_paths.add(str(resolved))
        
        raw = "@" + path_str
        is_dir = resolved.is_dir()
        
        if not resolved.exists():
            # Try with/without trailing slash
            alt = Path(working_dir) / path_str.rstrip("/")
            alt = alt.resolve()
            if alt.exists():
                resolved = alt
                is_dir = alt.is_dir()
            else:
                ref = FileReference(raw, path_str, resolved, False)
                ref.error = f"not found: {path_str}"
                refs.append(ref)
                continue
        
        ref = FileReference(raw, path_str, resolved, is_dir)
        refs.append(ref)
    
    return refs


def expand_at_references(text, working_dir):
    """
    Parse @-references in text, read their content, and return:
      - expanded_text: original text with @refs replaced by short labels
      - context_block: file contents to prepend to the message
      - refs: list of FileReference objects
    """
    refs = parse_at_references(text, working_dir)
    if not refs:
        return text, "", refs
    
    context_parts = []
    total_size = 0
    expanded_text = text
    
    for ref in refs:
        if ref.error:
            continue
        
        if ref.is_dir:
            # Directory: tree + files
            tree = _build_tree(ref.resolved_path)
            dir_files = _collect_dir_files(ref.resolved_path)
            
            parts = [f"--- @{ref.path_str} (directory) ---"]
            parts.append(f"Tree:\n{tree}")
            parts.append("")
            
            for rel_name, fpath in dir_files:
                if total_size >= _MAX_TOTAL_SIZE:
                    parts.append(f"... [context size limit reached, {total_size} bytes] ...")
                    break
                content, _ = _read_file_content(fpath, _MAX_FILE_SIZE)
                if content is not None:
                    parts.append(f"--- {ref.path_str}/{rel_name} ---")
                    parts.append(content)
                    parts.append("")
                    total_size += len(content)
            
            parts.append(f"--- end @{ref.path_str} ---")
            ref.content = "\n".join(parts)
            context_parts.append(ref.content)
        else:
            # Single file
            content, truncated = _read_file_content(ref.resolved_path)
            if content is not None:
                block = f"--- @{ref.path_str} ---\n{content}\n--- end @{ref.path_str} ---"
                ref.content = block
                context_parts.append(block)
                total_size += len(content)
            else:
                ref.error = f"cannot read: {ref.path_str}"
    
    if not context_parts:
        return text, "", refs

    context_block = "\n\n".join(context_parts)

    logger.info(
        "file_context: %d refs, %d bytes injected (working_dir=%s)",
        len(refs), len(context_block), working_dir,
    )
    return expanded_text, context_block, refs

