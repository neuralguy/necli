"""
Реестр инструментов и диспетчер выполнения.
"""

import time
from collections.abc import Callable

from config import READ_ONLY_TOOLS as _READ_ONLY_CANONICAL
from logger import logger, payload_preview
from tools.expand_result import execute_expand_tool_result
from tools.file_ops import (
    create_file,
    docx,
    execute_grep,
    patch_file,
    pptx,
    read,
)
from tools.image_search import execute_image_search
from tools.memory_tool import memory
from tools.models import ToolCall, ToolResult
from tools.poll import execute_poll
from tools.shell import execute_shell
from tools.skill_tool import execute_skill
from tools.subagent import execute_subagent
from tools.web_fetch import execute_web_fetch
from tools.web_search import execute_web_search


# LSP-инструменты импортируются лениво, чтобы избежать циркулярного импорта:
# apis/lsp_client.py импортирует tools.models, что инициализирует tools/__init__.py,
# который импортирует tools.registry. Регистрируем тонкие обёртки.
def _lsp_ref(call):
    from apis.lsp_client import execute_lsp_references

    return execute_lsp_references(call)


def _lsp_diag(call):
    from apis.lsp_client import execute_lsp_diagnostics

    return execute_lsp_diagnostics(call)


# Маппинг имя → функция-обработчик
TOOL_REGISTRY: dict[str, Callable] = {
    "shell": execute_shell,
    "read": read,
    "grep": execute_grep,
    "patch_file": patch_file,
    "create_file": create_file,
    "docx": docx,
    "pptx": pptx,
    "poll": execute_poll,
    "skill": execute_skill,
    "subagent": execute_subagent,
    "web_search": execute_web_search,
    "web_fetch": execute_web_fetch,
    "image_search": execute_image_search,
    "expand_tool_result": execute_expand_tool_result,
    "memory": memory,
    "lsp_references": _lsp_ref,
    "lsp_diagnostics": _lsp_diag,
}


def _hook_tool_input(call: ToolCall) -> dict:
    """Готовит tool_input для hook payload из ToolCall."""
    ti = dict(call.args or {})
    if call.command and "command" not in ti:
        ti["command"] = call.command
    return ti


def _run_pre_tool_hooks(call: ToolCall) -> ToolResult | None:
    """PreToolUse: возвращает blocked-ToolResult или None (продолжать)."""
    try:
        from config.hooks import has_hooks

        if not has_hooks("PreToolUse"):
            return None
        from hooks import run_hooks
        from tools._paths import get_working_dir

        outcome = run_hooks(
            "PreToolUse",
            {"tool_name": call.tool_name, "tool_input": _hook_tool_input(call)},
            working_dir=get_working_dir(),
        )
        if outcome.blocked:
            reason = outcome.block_reason or "Blocked by PreToolUse hook."
            return ToolResult(
                name=call.tool_name,
                status="error",
                output=f"⚠︎ {reason}",
                exit_code=2,
                command=call.command,
            )
    except Exception as e:
        logger.opt(exception=True).warning("PreToolUse hook error ignored: {}", e)
    return None


def _run_post_tool_hooks(call: ToolCall, result: ToolResult) -> None:
    """PostToolUse: может подмешать additionalContext в вывод инструмента."""
    try:
        from config.hooks import has_hooks

        if not has_hooks("PostToolUse"):
            return
        from hooks import run_hooks
        from tools._paths import get_working_dir

        outcome = run_hooks(
            "PostToolUse",
            {
                "tool_name": call.tool_name,
                "tool_input": _hook_tool_input(call),
                "tool_response": {
                    "status": result.status,
                    "exit_code": result.exit_code,
                },
            },
            working_dir=get_working_dir(),
        )
        ctx = outcome.context_text
        if ctx:
            sep = "\n\n" if result.output else ""
            result.output = f"{result.output}{sep}[hook] {ctx}"
    except Exception as e:
        logger.opt(exception=True).warning("PostToolUse hook error ignored: {}", e)


def get_disabled_tools() -> set[str]:
    """Возвращает сохранённый набор отключённых пользователем инструментов."""
    try:
        from config.settings import get

        value = get("disabled_tools", [])
        return (
            {name for name in value if isinstance(name, str)}
            if isinstance(value, list)
            else set()
        )
    except Exception:
        logger.debug("disabled_tools lookup failed", exc_info=True)
        return set()


def is_tool_enabled(tool_name: str) -> bool:
    return tool_name not in get_disabled_tools()


def set_tool_enabled(tool_name: str, enabled: bool) -> None:
    """Сохраняет пользовательское состояние одного инструмента."""
    from config.settings import set_value

    disabled = get_disabled_tools()
    if enabled:
        disabled.discard(tool_name)
    else:
        disabled.add(tool_name)
    set_value("disabled_tools", sorted(disabled))


def list_tool_info() -> list[dict[str, object]]:
    """Список зарегистрированных инструментов с описаниями и состоянием."""
    descriptions: dict[str, str] = {}
    try:
        from apis.tool_schemas import TOOL_SCHEMAS

        schemas = list(TOOL_SCHEMAS)
        try:
            from apis.mcp_client import get_mcp_tool_schemas

            schemas.extend(get_mcp_tool_schemas())
        except Exception:
            pass
        descriptions = {
            schema["function"]["name"]: " ".join(
                schema["function"].get("description", "").split()
            ).split(". ", 1)[0]
            for schema in schemas
        }
    except Exception:
        logger.debug("tool descriptions lookup failed", exc_info=True)
    return [
        {
            "name": name,
            "description": descriptions.get(name, "External tool"),
            "enabled": is_tool_enabled(name),
        }
        for name in sorted(TOOL_REGISTRY)
    ]


def execute_call(call: ToolCall) -> ToolResult:
    """Выполняет вызов инструмента через реестр."""
    if not is_tool_enabled(call.tool_name):
        return ToolResult(
            name=call.tool_name or "unknown",
            status="error",
            output=(
                f"Инструмент '{call.tool_name}' отключён пользователем. Включите его через /tools."
            ),
            exit_code=1,
            command=call.command,
        )
    # PreToolUse hooks: могут заблокировать вызов до выполнения.
    blocked = _run_pre_tool_hooks(call)
    if blocked is not None:
        return blocked

    handler = TOOL_REGISTRY.get(call.tool_name)

    if handler is None:
        logger.warning(
            "execute_call: unknown tool '{}' (args_keys={})",
            call.tool_name,
            list((call.args or {}).keys()),
        )
        return ToolResult(
            name=call.tool_name or "unknown",
            status="error",
            output=(
                f"Неизвестный инструмент: '{call.tool_name}'. "
                f"Доступны: {', '.join(sorted(TOOL_REGISTRY.keys()))}"
            ),
            exit_code=1,
            command=call.command,
        )

    # Валидация/нормализация args по схеме инструмента ДО вызова handler'а:
    # резолвит алиасы (new_name→new_path), коэрсит типы (line="5"→5) и даёт
    # модели точную диагностику вместо невнятного симптома из handler'а.
    from tools.arg_validation import validate_and_normalize

    norm_args, arg_error = validate_and_normalize(
        call.tool_name,
        call.args or {},
        command=call.command,
    )
    if arg_error is not None:
        logger.warning(
            "execute_call: invalid args for {}: {}", call.tool_name, arg_error
        )
        return ToolResult(
            name=call.tool_name,
            status="error",
            output=arg_error,
            exit_code=1,
            command=call.command,
        )
    call.args = norm_args

    # `patches` тоже отрезаем — без него предпросмотр огромный.
    args_preview = {
        k: (v if not isinstance(v, str) or len(v) <= 120 else v[:120] + "…")
        for k, v in (call.args or {}).items()
        if k not in ("content", "b64", "patches")
    }
    logger.debug("→ tool {} args={}", call.tool_name, args_preview)
    t0 = time.monotonic()
    try:
        result = handler(call)
    except Exception as e:
        logger.opt(exception=True).error(
            "✗ tool {} raised {}: {}",
            call.tool_name,
            type(e).__name__,
            e,
        )
        err = ToolResult(
            name=call.tool_name,
            status="error",
            output=f"Внутренняя ошибка инструмента: {type(e).__name__}: {e}",
            exit_code=1,
            command=call.command,
        )
        err.elapsed = time.monotonic() - t0
        return err
    # Контракт 7.1: ToolResult.elapsed выставляется ВСЕГДА в одной точке —
    # здесь, в центральном диспетчере. Если handler уже выставил ненулевое
    # значение (например execute_and_show меряет дополнительно UI-обвязку) —
    # оставляем его. Иначе ставим наше измерение.
    if not getattr(result, "elapsed", 0):
        result.elapsed = time.monotonic() - t0
    if result.status == "error":
        from logger import error

        error(
            "tool.error",
            tool=call.tool_name,
            exit_code=result.exit_code,
            output_preview=payload_preview(result.output),
        )
    else:
        from logger import debug

        debug(
            "tool.success", tool=call.tool_name, output_chars=len(result.output or "")
        )
    # PostToolUse hooks: могут подмешать контекст в вывод.
    _run_post_tool_hooks(call, result)
    return result


# Канонический набор — config.READ_ONLY_TOOLS.
PLANNING_TOOLS = frozenset(
    _READ_ONLY_CANONICAL | {"poll", "skill", "web_search", "web_fetch"}
)
SWARM_TOOLS = frozenset(PLANNING_TOOLS | {"shell", "subagent"})


def is_tool_allowed(
    tool_name: str,
    mode: str,
    active_skills: set[str] | None = None,
    args: dict | None = None,
) -> bool:
    if not is_tool_enabled(tool_name):
        return False
    if mode == "agent":
        return True
    if tool_name == "memory":
        return str((args or {}).get("action", "")).lower() in ("list", "read")
    if mode in ("swarm", "auto"):
        return tool_name in SWARM_TOOLS
    return tool_name in PLANNING_TOOLS


def build_blocked_result(call: ToolCall, mode: str = "planning") -> ToolResult:
    """Создаёт ToolResult для инструмента, запрещённого настройками или режимом."""
    if not is_tool_enabled(call.tool_name):
        return ToolResult(
            name=call.tool_name,
            status="error",
            output=(
                f"Инструмент '{call.tool_name}' отключён пользователем. Включите его через /tools."
            ),
            exit_code=1,
            command=call.command,
        )
    allowed = SWARM_TOOLS if mode in ("swarm", "auto") else PLANNING_TOOLS
    allowed_human = ", ".join(sorted(allowed))
    return ToolResult(
        name=call.tool_name,
        status="error",
        output=(
            f"Tool '{call.tool_name}' is not allowed in {mode} mode. "
            f"Only {allowed_human} are available."
        ),
        exit_code=1,
        command=call.command,
    )


def list_tools() -> list[str]:
    """Возвращает список доступных инструментов."""
    return sorted(TOOL_REGISTRY.keys())


__all__ = [
    "PLANNING_TOOLS",
    "SWARM_TOOLS",
    "TOOL_REGISTRY",
    "build_blocked_result",
    "execute_call",
    "get_disabled_tools",
    "is_tool_allowed",
    "is_tool_enabled",
    "list_tool_info",
    "list_tools",
    "set_tool_enabled",
]
