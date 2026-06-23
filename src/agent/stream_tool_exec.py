"""Single-tool execution for LiveStream — extracted from agent/stream.py.

Главная цель — НЕ ДОПУСТИТЬ silent drop: если parse_call_block вернул None
(битый JSON / неизвестный tool / отсутствует path) — показать ошибку
пользователю и вернуть её модели как ToolResult, а не молча проглотить.
"""

import logging
import time
from pathlib import Path

import tools
from tools._paths import resolve_path
from tools.call_parser import parse_call_block as _parse_call_block
from tools.registry import is_tool_allowed, build_blocked_result
from agent.executor import execute_and_show
from agent.display import show_command, show_tool_combined

logger = logging.getLogger(__name__)


def _precheck_create_file(stream, complete) -> bool:
    """Если create_file целится в существующий файл — формируем error и просим early-abort.

    Возвращает True если pre-check сработал (стрим будет прерван).
    """
    if complete.tool_name != "create_file":
        return False
    call = _parse_call_block(
        complete.tool_name, complete.attrs_header,
        complete.body, complete.raw,
    )
    if call is None:
        return False
    path_str = (call.args or {}).get("path") or ""
    if not path_str:
        return False
    try:
        abs_path = Path(resolve_path(path_str))
    except Exception:
        return False
    if not abs_path.exists():
        return False
    err = tools.ToolResult(
        name="create_file",
        status="error",
        output=(
            f"File already exists: {path_str}. "
            f"Use write_file to overwrite or patch_file to modify."
        ),
        exit_code=1,
        command=call.command,
    )
    show_tool_combined(
        call, err,
        subtitle="[bold red]\u25a0 aborted: file exists[/bold red]",
    )
    from agent.loop import _tool_call_identity
    stream.request_early_abort(err, _tool_call_identity(call))
    logger.info("early abort: create_file on existing %s", path_str)
    return True


def _build_parse_error_result(tool_name: str, body: str, raw: str, reason: str) -> tools.ToolResult:
    """Создаёт ToolResult-ошибку для непарсящегося tool-блока."""
    short_body = (body or "").strip()
    if len(short_body) > 200:
        short_body = short_body[:200] + "..."
    return tools.ToolResult(
        name=tool_name or "unknown",
        status="error",
        output=(
            f"Parse error: {reason}\n"
            f"Tool: {tool_name}\n"
            f"Body (first 200 chars): {short_body}\n"
            f"Hint: check the JSON syntax in the block body, "
            f"presence of path in the header for write/create/patch, "
            f"and closing of the fenced block."
        ),
        exit_code=1,
        command=tool_name or "parse_error",
    )


def _diagnose_parse_failure(tool_name: str, attrs_header: str, body: str) -> str:
    """Пытается понять, ПОЧЕМУ parse_call_block вернул None — для понятного сообщения."""
    from tools.call_parser import NAMED_TOOLS, _CONTENT_TOOLS, _PATCH_TOOLS

    if tool_name not in NAMED_TOOLS:
        return f"unknown tool '{tool_name}'"

    if tool_name in _CONTENT_TOOLS or tool_name in _PATCH_TOOLS:
        if 'path=' not in (attrs_header or ''):
            return f"'{tool_name}' requires path=\"...\" in the fence header"

    stripped = (body or "").strip()
    if not stripped:
        if tool_name not in _CONTENT_TOOLS:
            return "empty block body"
    elif stripped.startswith('{') or stripped.startswith('['):
        return "body looks like JSON but does not parse — check quotes, commas, escaping"

    return "unknown reason (see body above)"


def handle_complete_tool(stream, complete) -> bool:
    """Обрабатывает один complete-блок: парсит, исполняет или показывает ошибку.

    Возвращает True если блок реально исполнен (для инкремента счётчика).
    КЛЮЧЕВОЕ ОТЛИЧИЕ от старой логики — если call=None, мы НЕ молчим:
    показываем error-панель и добавляем ToolResult в inline_results.
    """
    # think — это не исполняемый инструмент, а отображаемая мысль.
    # Native function-calling провайдеры присылают его как обычный tool_call
    # с args={"thought": "..."}, и он попадает сюда же как fenced-блок.
    # parse_think_blocks в LiveStream.on_text_update уже добавил мысль в
    # think_log и нарисовал thinking-panel. Не дублируем generic tool-рендер.
    if complete.tool_name in ("think", "plan"):
        return False

    if _precheck_create_file(stream, complete):
        return False

    # Skipped из-за interrupt
    if complete.body and stream.ctx.interrupted:
        call = _parse_call_block(
            complete.tool_name, complete.attrs_header,
            complete.body, complete.raw,
        )
        if call:
            show_command(
                call.command, tool_name=call.tool_name, args=call.args,
                subtitle="[bold yellow]\u25a0 skipped (interrupted)[/bold yellow]",
            )
        return False

    # Пустое тело допустимо для shorthand с attrs в шапке
    # (`:::call delete_file path="x.py"` без JSON body). Парсер сам разрулит:
    # если ни body, ни attrs не дают валидных args — вернёт None → нижняя
    # ветка ниже покажет parse error с диагностикой.
    if not complete.body and not (complete.attrs_header or "").strip():
        reason = "empty fenced block body"
        err = _build_parse_error_result(complete.tool_name, "", complete.raw, reason)
        show_tool_combined(
            tools.ToolCall(
                command=complete.tool_name, tool_name=complete.tool_name,
                args={}, raw=complete.raw,
            ),
            err,
            subtitle="[bold red]\u25a0 parse error[/bold red]",
        )
        stream.inline_results.append(err)
        stream.inline_call_keys.append((complete.tool_name, "{}"))
        return False

    from agent.stream import _tool_subtitle
    write_time = time.monotonic() - stream._last_block_end_time
    subtitle = _tool_subtitle(stream.model, write_time, complete.raw)


    # Factory: пересчитает subtitle с реальным output после выполнения.
    def _mk_subtitle(result):
        return _tool_subtitle(stream.model, write_time, complete.raw, result.output or "")

    if complete.tool_name == "subagent":
        # subagent выполняется в agent/loop.py — здесь только парсим/валидируем
        call = _parse_call_block(
            complete.tool_name, complete.attrs_header,
            complete.body, complete.raw,
        )
        if call is None:
            reason = _diagnose_parse_failure(
                complete.tool_name, complete.attrs_header, complete.body,
            )
            err = _build_parse_error_result(
                complete.tool_name, complete.body, complete.raw, reason,
            )
            show_tool_combined(
                tools.ToolCall(
                    command=complete.tool_name, tool_name=complete.tool_name,
                    args={}, raw=complete.raw,
                ),
                err,
                subtitle="[bold red]\u25a0 parse error[/bold red]",
            )
            stream.inline_results.append(err)
            stream.inline_call_keys.append((complete.tool_name, "{}"))
            return False
        return True

    call = _parse_call_block(
        complete.tool_name, complete.attrs_header,
        complete.body, complete.raw,
    )
    if call is None:
        reason = _diagnose_parse_failure(
            complete.tool_name, complete.attrs_header, complete.body,
        )
        logger.warning(
            "Parse failure for tool=%s reason=%s body_len=%d",
            complete.tool_name, reason, len(complete.body or ""),
        )
        err = _build_parse_error_result(
            complete.tool_name, complete.body, complete.raw, reason,
        )
        show_tool_combined(
            tools.ToolCall(
                command=complete.tool_name, tool_name=complete.tool_name,
                args={}, raw=complete.raw,
            ),
            err,
            subtitle="[bold red]\u25a0 parse error[/bold red]",
        )
        stream.inline_results.append(err)
        stream.inline_call_keys.append((complete.tool_name, "{}"))
        return False

    from agent.loop import _tool_call_identity

    if complete.tool_name == "web_search":
        res = execute_and_show(
            [call], event_handler=stream.ctx.event_handler,
            subtitle=subtitle, subtitle_factory=_mk_subtitle,
        )
        stream.inline_results.extend(res)
        stream.inline_call_keys.append(_tool_call_identity(call))
        return True

    if not is_tool_allowed(call.tool_name, stream.ctx.mode):
        blocked = build_blocked_result(call, stream.ctx.mode)
        show_tool_combined(call, blocked, subtitle=_mk_subtitle(blocked))
        stream.inline_results.append(blocked)
        stream.inline_call_keys.append(_tool_call_identity(call))
        return True

    res = execute_and_show(
        [call], event_handler=stream.ctx.event_handler,
        subtitle=subtitle, subtitle_factory=_mk_subtitle,
    )
    stream.inline_results.extend(res)
    stream.inline_call_keys.append(_tool_call_identity(call))
    if any(r.fatal for r in res):
        stream.ctx.interrupted = True
    return True