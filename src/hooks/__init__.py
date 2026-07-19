"""Пользовательские event-hooks.

Порт урезанной hooks-системы Claude Code под архитектуру necli. Позволяет
пользователю навешивать shell-команды / HTTP-вызовы на события жизненного
цикла агента — без правки кода ядра.

События (см. HOOK_EVENTS):
  PreToolUse        — перед выполнением инструмента; может заблокировать/изменить.
  PostToolUse       — после выполнения; может подмешать контекст в историю.
  UserPromptSubmit  — когда пользователь отправил сообщение; может добавить контекст
                      или заблокировать отправку.
  Stop              — агент завершил раунд (нет tool calls в ответе).
  SessionStart      — старт сессии.
  SessionEnd        — конец сессии.

Конфиг живёт в .data/hooks.json (см. config/hooks.py).

Публичный API:
  run_hooks(event, payload, *, working_dir) -> HookOutcome
"""

from .runner import run_hooks
from .schema import (
    HOOK_EVENTS,
    HookEvent,
    HookMatcher,
    HookOutcome,
    HookSpec,
)

__all__ = [
    "HOOK_EVENTS",
    "HookEvent",
    "HookMatcher",
    "HookOutcome",
    "HookSpec",
    "run_hooks",
]
