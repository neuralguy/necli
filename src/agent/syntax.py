"""Подсветка синтаксиса и определение лексеров для вывода."""

import json
import re

_EXT_LEXER_MAP = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "xml": "xml",
    "html": "html",
    "css": "css",
    "sh": "bash",
    "bash": "bash",
    "rs": "rust",
    "go": "go",
    "rb": "ruby",
    "cpp": "cpp",
    "c": "c",
    "h": "c",
    "hpp": "cpp",
    "java": "java",
    "kt": "kotlin",
    "sql": "sql",
    "md": "markdown",
    "conf": "ini",
    "cfg": "ini",
    "ini": "ini",
    "env": "bash",
    "dockerfile": "docker",
    "tsx": "typescript",
    "jsx": "javascript",
    "vue": "vue",
    "svelte": "html",
    "scss": "scss",
    "less": "less",
    "lua": "lua",
    "php": "php",
    "swift": "swift",
    "r": "r",
    "pl": "perl",
    "tf": "terraform",
    "hcl": "terraform",
    "zig": "zig",
    "nim": "nim",
    "dart": "dart",
}

_PLAIN_OUTPUT_CMDS = frozenset(
    {
        "echo",
        "printf",
        "ls",
        "ll",
        "dir",
        "pwd",
        "whoami",
        "date",
        "uptime",
        "free",
        "df",
        "du",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "env",
        "printenv",
        "uname",
        "ps",
        "top",
        "htop",
        "kill",
        "jobs",
        "fg",
        "bg",
        "pip",
        "pip3",
        "npm",
        "yarn",
        "pnpm",
        "cargo",
        "go",
        "git",
        "docker",
        "kubectl",
        "make",
        "cmake",
        "apt",
        "apt-get",
        "brew",
        "pacman",
        "yum",
        "dnf",
        "systemctl",
        "journalctl",
        "service",
        "curl",
        "wget",
        "ssh",
        "scp",
        "rsync",
        "chmod",
        "chown",
        "chgrp",
        "ln",
        "cp",
        "mv",
        "mkdir",
        "touch",
        "file",
        "stat",
        "id",
        "groups",
    }
)

_FILE_VIEWER_CMDS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "bat",
        "batcat",
        "sed",
        "awk",
        "tac",
        "nl",
    }
)

_NATIVE_TOOL_LEXERS = {
    "read_files": None,
    "read_file": None,
    "write_file": None,
    "patch_file": "diff",
    "create_file": None,
    "delete_file": None,
    "rename_file": None,
    "copy_file": None,
    "move_file": None,
    "ls": None,
    "tree": None,
    "mkdir": None,
    "rmdir": None,
    "find_files": None,
    "grep_files": None,
}


def guess_output_lexer(
    output: str, cmd: str = "", tool_name: str = "shell"
) -> str | None:
    """Угадывает лексер для подсветки вывода инструмента."""
    stripped = output.strip()
    if not stripped:
        return None

    if tool_name != "shell":
        if tool_name == "patch_file" and "---" in stripped:
            return "diff"
        if tool_name in ("read_file", "read_files"):
            header_match = re.match(r"\[([^\]]+?)(?:\s*·|\])", stripped)
            if header_match:
                file_path = header_match.group(1).strip()
                ext_match = re.search(r"\.(\w+)$", file_path)
                if ext_match:
                    ext = ext_match.group(1).lower()
                    if ext in _EXT_LEXER_MAP:
                        return _EXT_LEXER_MAP[ext]
        if tool_name == "grep_files":
            return None
        return None

    cmd_parts = cmd.strip().split()
    cmd_first = cmd_parts[0] if cmd_parts else ""
    while cmd_first in ("sudo", "env", "nice", "nohup", "time", "strace"):
        cmd_parts = cmd_parts[1:]
        cmd_first = cmd_parts[0] if cmd_parts else ""

    if cmd_first in (
        "python3",
        "python",
        "node",
        "ruby",
        "perl",
        "python3.10",
        "python3.11",
        "python3.12",
        "python3.13",
    ):
        if "Traceback (most recent call last):" in stripped:
            return "pytb"
        return None

    if cmd_first in _PLAIN_OUTPUT_CMDS:
        if cmd_first == "git" and len(cmd_parts) > 1:
            if cmd_parts[1] in ("diff", "show"):
                return "diff"
        return None

    if cmd_first in _FILE_VIEWER_CMDS:
        ext_match = re.search(r"\.(\w+)(?:\s|$|\||>|;)", cmd)
        if ext_match:
            ext = ext_match.group(1).lower()
            if ext in _EXT_LEXER_MAP:
                return _EXT_LEXER_MAP[ext]

    if cmd_first in ("grep", "rg", "ag", "ack"):
        return None

    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except Exception:
            pass

    if stripped.startswith("<?xml") or (
        stripped.startswith("<") and "xmlns" in stripped[:200]
    ):
        return "xml"
    if stripped.startswith("<!DOCTYPE") or stripped.startswith("<html"):
        return "html"
    if stripped.startswith("diff --git") or stripped.startswith("--- a/"):
        return "diff"
    if "Traceback (most recent call last):" in stripped:
        return "pytb"
    if cmd_first in ("find", "locate", "which", "whereis", "type"):
        return None
    return None
