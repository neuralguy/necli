"""
Реестр инструментов и диспетчер выполнения.
"""

import time
from collections.abc import Callable

from config import READ_ONLY_TOOLS as _READ_ONLY_CANONICAL
from logger import logger
from tools.models import ToolCall, ToolResult
from tools.shell import execute_shell


from tools.file_ops import (
    read_files,
    write_file,
    patch_file,
    create_file,
    delete_file,
    rename_file,
    copy_file,
    move_file,
    create_docx,
    docx_screenshot,
    apply_diff,
)
from tools.dir_ops import (
    ls,
    tree,
    mkdir,
    rmdir,
    find_files,
    grep_files,
)
from tools.poll import execute_poll
from tools.skill_tool import execute_skill
from tools.ssh import execute_ssh
from tools.subagent import execute_subagent
from tools.web_search import execute_web_search
from tools.image_search import execute_image_search
from tools.expand_result import execute_expand_tool_result
from tools.memory_tool import memory_write, memory_list, memory_read


# LSP-инструменты импортируются лениво, чтобы избежать циркулярного импорта:
# apis/lsp_client.py импортирует tools.models, что инициализирует tools/__init__.py,
# который импортирует tools.registry. Регистрируем тонкие обёртки.
def _lsp_def(call):
    from apis.lsp_client import execute_lsp_definition
    return execute_lsp_definition(call)


def _lsp_ref(call):
    from apis.lsp_client import execute_lsp_references
    return execute_lsp_references(call)


def _lsp_hover(call):
    from apis.lsp_client import execute_lsp_hover
    return execute_lsp_hover(call)


def _lsp_diag(call):
    from apis.lsp_client import execute_lsp_diagnostics
    return execute_lsp_diagnostics(call)

# Маппинг имя → функция-обработчик
TOOL_REGISTRY: dict[str, Callable] = {
    "shell": execute_shell,
    "read_files": read_files,
    "read_file": read_files,  # alias
    "write_file": write_file,
    "patch_file": patch_file,
    "create_file": create_file,
    "delete_file": delete_file,
    "rename_file": rename_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "create_docx": create_docx,
    "docx_screenshot": docx_screenshot,
    "apply_diff": apply_diff,
    "ls": ls,
    "tree": tree,
    "mkdir": mkdir,
    "rmdir": rmdir,
    "find_files": find_files,
    "grep_files": grep_files,
    "poll": execute_poll,
    "skill": execute_skill,
    "ssh": execute_ssh,
    "subagent": execute_subagent,
    "web_search": execute_web_search,
    "image_search": execute_image_search,
    "expand_tool_result": execute_expand_tool_result,
    "memory_write": memory_write,
    "memory_list": memory_list,
    "memory_read": memory_read,
    "lsp_definition": _lsp_def,
    "lsp_references": _lsp_ref,
    "lsp_hover": _lsp_hover,
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
                output=f"⛔ {reason}",
                exit_code=2,
                command=call.command,
            )
    except Exception as e:  # noqa: BLE001 — hooks никогда не роняют выполнение
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
                "tool_response": {"status": result.status, "exit_code": result.exit_code},
            },
            working_dir=get_working_dir(),
        )
        ctx = outcome.context_text
        if ctx:
            sep = "\n\n" if result.output else ""
            result.output = f"{result.output}{sep}[hook] {ctx}"
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).warning("PostToolUse hook error ignored: {}", e)


def execute_call(call: ToolCall) -> ToolResult:
    """Выполняет вызов инструмента через реестр."""
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

    # `patches` тоже отрезаем — без него предпросмотр огромный.
    args_preview = {k: (v if not isinstance(v, str) or len(v) <= 120 else v[:120] + "…")
                    for k, v in (call.args or {}).items()
                    if k not in ("content", "b64", "insert", "replace", "find", "patches")}
    logger.debug("→ tool {} args={}", call.tool_name, args_preview)
    t0 = time.monotonic()
    try:
        result = handler(call)
    except Exception as e:
        logger.opt(exception=True).error(
            "✗ tool {} raised {}: {}", call.tool_name, type(e).__name__, e,
        )
        err = ToolResult(
            name=call.tool_name, status="error",
            output=f"Внутренняя ошибка инструмента: {type(e).__name__}: {e}",
            exit_code=1, command=call.command,
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
        logger.warning(
            "← tool {} ERROR exit={} out={!r}",
            call.tool_name, result.exit_code, (result.output or "")[:200],
        )
    else:
        logger.debug("← tool {} ok ({}b)", call.tool_name, len(result.output or ""))
    # PostToolUse hooks: могут подмешать контекст в вывод.
    _run_post_tool_hooks(call, result)
    return result


# Канонический набор — config.READ_ONLY_TOOLS. Алиас "read_file" для
# обратной совместимости с моделями, которые иногда называют его так.
PLANNING_TOOLS = frozenset(_READ_ONLY_CANONICAL | {"read_file"})
READ_ONLY_TOOLS = PLANNING_TOOLS

_PLANNING_TOOLS_HUMAN = ", ".join(sorted(_READ_ONLY_CANONICAL))


def is_tool_allowed(tool_name: str, mode: str) -> bool:
    if mode == "agent":
        return True
    return tool_name in PLANNING_TOOLS


def build_blocked_result(call: ToolCall) -> ToolResult:
    """Создаёт ToolResult для инструмента, запрещённого в planning mode."""
    return ToolResult(
        name=call.tool_name,
        status="error",
        output=(
            f"Tool '{call.tool_name}' is not allowed in planning mode. "
            f"Only {_PLANNING_TOOLS_HUMAN} are available."
        ),
        exit_code=1,
        command=call.command,
    )


def list_tools() -> list[str]:
    """Возвращает список доступных инструментов."""
    return sorted(TOOL_REGISTRY.keys())