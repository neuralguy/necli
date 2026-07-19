"""Схемы и разбор конфигурации hooks.

Формат .data/hooks.json (совместим по духу с claude-code settings.hooks):

  {
    "PreToolUse": [
      {
        "matcher": "shell",                # tool_name (или '*' / пусто = любой)
        "hooks": [
          {
            "type": "command",
            "command": "scripts/guard.sh",
            "if": "shell(git push *)",     # permission-style фильтр (опц.)
            "timeout": 10,                  # сек (опц.)
            "async": false                  # не блокировать (опц.)
          }
        ]
      }
    ],
    "PostToolUse": [...],
    "UserPromptSubmit": [...],
    "Stop": [...],
    "SessionStart": [...],
    "SessionEnd": [...]
  }

Также поддержан "плоский" формат (без обёртки matcher):
  { "Stop": [ { "type": "command", "command": "..." } ] }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HookEvent = str

HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
    "SessionStart",
    "SessionEnd",
)

HookType = Literal["command", "http"]


@dataclass
class HookSpec:
    """Одиночный hook (одна команда/запрос)."""

    type: HookType = "command"
    command: str = ""            # для type=command
    url: str = ""                # для type=http
    if_: str | None = None    # permission-style фильтр ('if' в JSON)
    timeout: float = 30.0
    is_async: bool = False       # 'async' в JSON — не блокировать выполнение
    headers: dict[str, str] = field(default_factory=dict)  # для http

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HookSpec:
        t = str(d.get("type", "command"))
        if t not in ("command", "http"):
            raise ValueError(f"unsupported hook type: {t!r}")
        timeout = d.get("timeout", 30)
        try:
            timeout = float(timeout)
            if timeout <= 0:
                timeout = 30.0
        except (TypeError, ValueError):
            timeout = 30.0
        return cls(
            type=t,  # type: ignore[arg-type]
            command=str(d.get("command", "")),
            url=str(d.get("url", "")),
            if_=(str(d["if"]) if d.get("if") else None),
            timeout=timeout,
            is_async=bool(d.get("async", False)),
            headers={str(k): str(v) for k, v in (d.get("headers") or {}).items()},
        )

    def validate(self) -> None:
        if self.type == "command" and not self.command.strip():
            raise ValueError("command hook requires non-empty 'command'")
        if self.type == "http" and not self.url.strip():
            raise ValueError("http hook requires non-empty 'url'")


@dataclass
class HookMatcher:
    """Группа hooks с общим matcher по tool_name."""

    matcher: str = "*"  # tool_name pattern: точное имя, '*' = любой, '' = любой
    hooks: list[HookSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HookMatcher:
        raw_hooks = d.get("hooks") or []
        hooks = [HookSpec.from_dict(h) for h in raw_hooks]
        return cls(matcher=str(d.get("matcher", "*") or "*"), hooks=hooks)


@dataclass
class HookOutcome:
    """Сводный результат прогона всех hooks события.

    blocked            — событие/инструмент заблокирован (decision=block или exit 2).
    block_reason       — причина блокировки (отдаётся модели/пользователю).
    additional_context — текст, подмешиваемый в историю (PostToolUse / UserPromptSubmit).
    system_messages    — сообщения для отображения пользователю.
    stop               — попросить агента остановиться (continue=false).
    """

    blocked: bool = False
    block_reason: str = ""
    additional_context: list[str] = field(default_factory=list)
    system_messages: list[str] = field(default_factory=list)
    stop: bool = False

    @property
    def context_text(self) -> str:
        return "\n".join(c for c in self.additional_context if c.strip())
