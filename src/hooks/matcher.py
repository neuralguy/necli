"""Сопоставление hooks с конкретным вызовом.

Два уровня фильтрации (как в claude-code):
  1. matcher  — по tool_name группы HookMatcher ('shell', '*', '' = любой).
  2. if       — permission-style правило, проверяемое против tool_name + аргументов.

Синтаксис `if` (упрощённый permission-rule):
  "shell"                — любой вызов shell
  "shell(git push *)"    — shell, чья команда матчит glob 'git push *'
  "write_file(*.py)"     — write_file, чей путь/команда матчит '*.py'
  "*"                    — что угодно
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

_RULE_RE = re.compile(r"^\s*([A-Za-z_*][\w*]*)\s*(?:\(([^)]*)\))?\s*$")


def matcher_matches(matcher: str, tool_name: str) -> bool:
    """Совпадение matcher группы с именем инструмента."""
    m = (matcher or "").strip()
    if m in ("", "*"):
        return True
    # matcher может быть списком через '|' (как regexp-alternation в claude-code)
    if "|" in m:
        return any(matcher_matches(part, tool_name) for part in m.split("|"))
    return fnmatch.fnmatch(tool_name, m)


def _arg_haystack(tool_name: str, tool_input: dict[str, Any] | None) -> list[str]:
    """Строки-кандидаты для матчинга аргумента правила.

    Берём command и наиболее вероятные «путь/цель»-поля, чтобы правило
    'write_file(*.py)' или 'shell(git *)' работало интуитивно.
    """
    ti = tool_input or {}
    out: list[str] = []
    cmd = ti.get("command")
    if isinstance(cmd, str) and cmd:
        out.append(cmd)
    for key in ("path", "file", "file_path", "target", "url", "query", "name"):
        v = ti.get(key)
        if isinstance(v, str) and v:
            out.append(v)
    # paths: список
    paths = ti.get("paths")
    if isinstance(paths, (list, tuple)):
        out.extend(str(p) for p in paths if p)
    return out


def if_matches(rule: str | None, tool_name: str, tool_input: dict[str, Any] | None) -> bool:
    """Проверяет permission-style правило `if` против вызова."""
    if not rule or not rule.strip():
        return True
    parsed = _RULE_RE.match(rule.strip())
    if not parsed:
        # Невалидное правило — не матчим (безопасный дефолт: hook не сработает).
        return False
    rule_tool, rule_arg = parsed.group(1), parsed.group(2)

    if rule_tool not in ("*", tool_name) and not fnmatch.fnmatch(tool_name, rule_tool):
        return False

    if rule_arg is None or rule_arg.strip() in ("", "*"):
        return True

    pattern = rule_arg.strip()
    for hay in _arg_haystack(tool_name, tool_input):
        if fnmatch.fnmatch(hay, pattern) or pattern in hay:
            return True
    return False
