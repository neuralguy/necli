"""Система подтверждений для инструментов агента.

Три уровня хранения:
- forever: в .data/config.json под ключом 'tool_permissions'
- process: в памяти процесса CLI (живёт до выхода)
- session: в памяти, сбрасывается по /new

Значения:
- 'ask'   — спрашивать каждый раз (дефолт для всех)
- 'allow' — выполнять без подтверждения
- 'deny'  — отказывать без подтверждения

Приоритет проверки: session > process > forever > 'ask' (default).
"""

from __future__ import annotations

from typing import Literal

import config

Decision = Literal["ask", "allow", "deny"]
Scope = Literal["session", "process", "forever"]

_PROCESS: dict[str, Decision] = {}
_SESSION: dict[str, Decision] = {}


# ── чтение/запись forever-уровня ──

def _forever_all() -> dict[str, Decision]:
    raw = config.get("tool_permissions", {})
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if v in ("ask", "allow", "deny")}


def _set_forever(tool: str, decision: Decision) -> None:
    all_ = _forever_all()
    if decision == "ask":
        all_.pop(tool, None)
    else:
        all_[tool] = decision
    config.set_value("tool_permissions", all_)


# ── публичное API ──

def _lookup(tool: str) -> tuple[Decision, Scope] | None:
    """Находит эффективное решение и его уровень.

    Приоритет — scope-first, внутри уровня явный tool важнее "*":
    session explicit, session *, process explicit, process *,
    forever explicit, forever *. Возвращает None, если решения нет.
    """
    for scope, store in (
        ("session", _SESSION),
        ("process", _PROCESS),
        ("forever", _forever_all()),
    ):
        if tool in store:
            return store[tool], scope
        if "*" in store:
            return store["*"], scope
    return None

def get_decision(tool: str) -> Decision:
    """Возвращает текущее эффективное решение для инструмента.

    Wildcard "*" в любом уровне действует как fallback для всех tools,
    которые не имеют явного решения. Приоритет уровней сохраняется:
    более высокий уровень (session) побеждает даже своей звездой более
    низкий уровень (forever) с явным решением.
    """
    found = _lookup(tool)
    return found[0] if found else "ask"


def get_scope(tool: str) -> Scope | None:
    """Возвращает уровень, на котором установлено решение (None если ask по дефолту)."""
    found = _lookup(tool)
    return found[1] if found else None


def set_decision(tool: str, decision: Decision, scope: Scope) -> None:
    if decision == "ask":
        # Сброс на указанном уровне
        if scope == "session":
            _SESSION.pop(tool, None)
        elif scope == "process":
            _PROCESS.pop(tool, None)
        else:
            _set_forever(tool, "ask")
        return

    if scope == "session":
        _SESSION[tool] = decision
    elif scope == "process":
        _PROCESS[tool] = decision
    else:
        _set_forever(tool, decision)


def reset_tool(tool: str) -> None:
    """Сбрасывает все три уровня → 'ask' для конкретного инструмента."""
    _SESSION.pop(tool, None)
    _PROCESS.pop(tool, None)
    _set_forever(tool, "ask")


def reset_session() -> None:
    """Очищает только session-уровень. Вызывается по /new."""
    _SESSION.clear()


def reset_all() -> None:
    """Полный сброс всех уровней."""
    _SESSION.clear()
    _PROCESS.clear()
    config.set_value("tool_permissions", {})


