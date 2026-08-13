"""Tool call execution with terminal display."""

import asyncio
import contextvars
import re
import time
from functools import partial

from rich.panel import Panel
from rich.text import Text

import tools
from agent.display import (
    _w,
    print_static,
)
from config.themes import t
from logger import logger

#: Ключ динамической зоны под индикатор «инструмент выполняется». Раньше это
#: был rich Live с transient=True: спиннер исчезал, а итоговую шапку печатал
#: show_tool_combined.
_TOOL_ZONE = "tool"

_WRITE_TIME_RE = re.compile(r"@@WRITE_TIME=([\d.]+)@@")


def _extract_write_time(subtitle: str) -> float | None:
    """Достаёт streaming-время блока из маркера @@WRITE_TIME=N@@ в subtitle.

    Это время, которое модель потратила на стриминг тела tool-блока (тикает в
    live-индикаторе). Для контентных инструментов оно — осмысленный таймер, в
    отличие от мгновенного времени исполнения. None, если маркера нет.
    """
    if not subtitle:
        return None
    m = _WRITE_TIME_RE.search(subtitle)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None


def _working_detail(args: dict | None) -> str:
    """Короткое описание вызова для Working без предположений о его схеме."""
    data = args or {}
    value = data.get("path") or data.get("command") or data.get("prompt") or data.get("name") or ""
    lines = str(value).splitlines()
    return lines[0][:72] if lines else ""


def _execution_error(call: tools.ToolCall, error: Exception) -> tools.ToolResult:
    """Не дать сбою UI-обвязки оставить native tool_call без ответа."""
    logger.opt(exception=True).error(
        "tool execution wrapper failed for {}: {}",
        call.tool_name,
        error,
    )
    return tools.ToolResult(
        name=call.tool_name or "tool",
        status="error",
        output=f"Internal tool execution error: {type(error).__name__}: {error}",
        exit_code=1,
        command=call.command,
    )


# Инструменты, которые сами рисуют живой мультиплексный UI (своя динамическая
# зона Shell). Для них НЕЛЬЗЯ поднимать спиннер «Tool …s»: две зоны нарисовали
# бы над рамкой сразу два кадра об одном и том же. У subagent своя ветка в
# loop.py — поэтому гасим индикатор здесь по имени инструмента.
_SELF_RENDERING_TOOLS = frozenset({"subagent"})


def _show_poll_result(result: tools.ToolResult):
    output = result.output.strip()
    if not output:
        return
    text = Text()
    for line in output.split("\n"):
        if line.startswith("Q: "):
            text.append("  \u2753 ", style=f"bold {t('accent')}")
            text.append(line[3:], style=f"bold {t('accent')}")
        elif line.startswith("A: "):
            answer = line[3:]
            text.append("  \u2192 ", style=t("success"))
            text.append(answer, style=f"bold {t('success')}")
            text.append("\n")
        else:
            continue
    # output \u043d\u0435\u043f\u0443\u0441\u0442\u043e\u0439, \u043d\u043e \u043d\u0435 \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 Q:/A: \u0441\u0442\u0440\u043e\u043a (\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 \u043e\u0448\u0438\u0431\u043a\u0430 poll
    # "No questions provided" \u043f\u0440\u0438 \u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u043e\u043c \u0432\u044b\u0437\u043e\u0432\u0435) \u2192 text \u043f\u0443\u0441\u0442\u043e\u0439.
    # \u0411\u0435\u0437 \u044d\u0442\u043e\u0439 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 \u0440\u0438\u0441\u0443\u0435\u0442\u0441\u044f \u043f\u0443\u0441\u0442\u0430\u044f \u0440\u0430\u043c\u043a\u0430-\u043f\u0430\u043d\u0435\u043b\u044c \u256d\u2500\u2500\u256f.
    if not text.plain.strip():
        return
    print_static(
        Panel(
            text,
            border_style=t("accent"),
            padding=(0, 1),
            width=_w(),
        )
    )


def _execute_single(
    call: tools.ToolCall,
    event_handler=None,
    subtitle: str = "",
    subtitle_factory=None,
    suppress_display: bool = False,
) -> tools.ToolResult:
    if call.tool_name != "poll":
        from tools.registry import TOOL_REGISTRY

        if not call.tool_name.startswith("mcp__") and call.tool_name not in TOOL_REGISTRY:
            logger.warning("unknown tool requested: {} (skipping approval prompt)", call.tool_name)
            return tools.execute_call(call)
        from config.permissions import get_decision

        decision = get_decision(call.tool_name)
        if decision == "deny":
            logger.info("tool {} blocked by permission=deny", call.tool_name)
            return tools.ToolResult(
                name=call.tool_name,
                status="error",
                output=(
                    f"Tool '{call.tool_name}' is blocked by permission "
                    f"settings (deny). Manage via: /permissions"
                ),
                exit_code=1,
                command=call.command,
            )
        if decision == "ask":
            # Меню разрешения — оверлей нижней зоны, то есть корутина. Эта
            # функция синхронная и вызывается как из рабочего потока, так и из
            # самого loop'а, поэтому идём через мост; он различает оба случая.
            from commands.permission_prompt import confirm_tool_call_sync

            if not confirm_tool_call_sync(call):
                logger.info("tool {} denied by user via prompt", call.tool_name)
                return tools.ToolResult(
                    name=call.tool_name,
                    status="error",
                    output=(
                        f"User denied execution of '{call.tool_name}'. "
                        f"Take this into account and suggest an alternative."
                    ),
                    exit_code=1,
                    command=call.command,
                )

    from agent.loop import get_current_ctx

    _ctx = get_current_ctx()
    _silent = bool(_ctx and getattr(_ctx, "silent_console", False))

    if call.tool_name == "poll":
        single_line = bool(getattr(event_handler, "single_line_tools", False))
        if single_line:
            event_handler.on_tool_start(call, subtitle=subtitle)
        prompt_input = getattr(_ctx, "prompt_input", None) if _ctx else None
        if prompt_input is not None and hasattr(prompt_input, "set_activity_status"):
            try:
                prompt_input.set_activity_status("poll")
            except Exception:
                logger.debug("poll activity status set failed", exc_info=True)
        if not _silent:
            print_static(Text(""))
        from agent.working import current_working_round

        working = current_working_round()
        if working is not None:
            working.begin_call(call.tool_name)
        try:
            result = tools.execute_call(call)
        finally:
            if working is not None:
                working.finish_call(call.tool_name)
            if prompt_input is not None and hasattr(prompt_input, "set_activity_status"):
                try:
                    prompt_input.set_activity_status("working")
                except Exception:
                    logger.debug("poll activity status restore failed", exc_info=True)
        if single_line:
            event_handler.on_tool_result(result)
        elif not _silent:
            _show_poll_result(result)
        return result
    if event_handler is not None:
        event_handler.on_tool_start(call, subtitle=subtitle)

    t0 = time.monotonic()
    from agent.working import current_working_round

    working = current_working_round()
    if working is not None:
        detail = _working_detail(call.args)
        working.begin_call(call.tool_name, detail)
    try:
        result = tools.execute_call(call)
    finally:
        if working is not None:
            working.finish_call(call.tool_name)
    result.elapsed = time.monotonic() - t0

    from logger import SLOW_TOOL_THRESHOLD, info, warning

    # Emit tool.end event
    info(
        "tool.end",
        tool=call.tool_name,
        duration=result.elapsed,
        status=result.status,
        exit_code=result.exit_code,
    )

    # Slow tool warning
    if result.elapsed > SLOW_TOOL_THRESHOLD:
        warning(
            "tool.slow", tool=call.tool_name, duration=result.elapsed, command=call.command[:100]
        )

    logger.info(
        "tool_done: {} status={} exit={} elapsed={:.2f}s",
        call.tool_name,
        result.status,
        result.exit_code,
        result.elapsed,
    )

    final_subtitle = subtitle
    if subtitle_factory is not None:
        try:
            final_subtitle = subtitle_factory(result)
        except Exception:
            logger.debug("subtitle_factory failed for {}", call.tool_name, exc_info=True)

    # Для контентных инструментов (write/create/patch/docx/pptx) реальная «работа» —
    # это время, пока модель СТРИМИЛА тело блока (тикает в live-индикаторе), а
    # само исполнение почти мгновенно. Поэтому в финальном статичном выводе
    # показываем это streaming-время (@@WRITE_TIME=N@@ из subtitle), иначе таймер
    # схлопывался в 0.0s. Для shell/read оставляем реальное время исполнения.
    is_streamed_write = call.tool_name in ("create_file", "patch_file") or (
        call.tool_name in ("docx", "pptx")
        and str((call.args or {}).get("action") or "").lower() in ("create", "edit")
    )
    if is_streamed_write:
        wt = _extract_write_time(final_subtitle)
        if wt is not None and wt > result.elapsed:
            result.elapsed = wt

    if not suppress_display:
        if event_handler is not None:
            event_handler.on_tool_result(result)
        else:
            from agent.display import show_tool_combined

            show_tool_combined(call, result, subtitle=final_subtitle)

    if _ctx and _ctx.step_tracker:
        _ctx.step_tracker.record(call.tool_name, result.output, args=call.args)

    if _ctx is not None and _ctx.refresh_status is not None:
        try:
            _ctx.refresh_status()
        except Exception:
            logger.debug("status refresh after tool failed", exc_info=True)

    return result


def _execute_single_safe(
    call: tools.ToolCall,
    event_handler=None,
    **kwargs,
) -> tools.ToolResult:
    try:
        return _execute_single(call, event_handler, **kwargs)
    except Exception as error:
        return _execution_error(call, error)


def execute_and_show(
    calls: list[tools.ToolCall], event_handler=None, subtitle: str = "", subtitle_factory=None
) -> list[tools.ToolResult]:
    # Сохраняем исходный порядок: results[idx] = result
    indexed: dict[int, tools.ToolResult] = {}
    read_pairs: list[tuple[tools.ToolCall, tools.ToolResult]] = []

    combine_reads = not getattr(event_handler, "single_line_tools", False)
    for idx, call in enumerate(calls):
        if (
            call.tool_name == "read"
            and combine_reads
            and sum(1 for c in calls if c.tool_name == "read") >= 2
        ):
            # Несколько read — без индивидуального отображения
            result = _execute_single_safe(
                call,
                event_handler,
                subtitle=subtitle,
                subtitle_factory=subtitle_factory,
                suppress_display=True,
            )
            read_pairs.append((call, result))
            indexed[idx] = result
        else:
            result = _execute_single_safe(
                call, event_handler, subtitle=subtitle, subtitle_factory=subtitle_factory
            )
            indexed[idx] = result

    # Показываем все read одним компактным блоком
    if read_pairs:
        from agent.display import show_read_combined

        show_read_combined(read_pairs)

    results = [indexed[i] for i in range(len(calls))]
    return results


async def execute_and_show_async(
    calls: list[tools.ToolCall],
    event_handler=None,
    subtitle: str = "",
    subtitle_factory=None,
) -> list[tools.ToolResult]:
    from logger import info, log_span

    # Track batch
    info("tools.batch.start", count=len(calls))

    with log_span("tools.batch", count=len(calls)):
        return await _execute_and_show_async_impl(
            calls,
            event_handler=event_handler,
            subtitle=subtitle,
            subtitle_factory=subtitle_factory,
        )


async def _execute_and_show_async_impl(
    calls: list[tools.ToolCall],
    event_handler=None,
    subtitle: str = "",
    subtitle_factory=None,
) -> list[tools.ToolResult]:
    from agent.loop import get_current_ctx
    from tools.cancellation import CancellationScope, use_cancellation_scope

    loop = asyncio.get_running_loop()
    agent_ctx = get_current_ctx()
    # Сохраняем исходный порядок: results[idx] = result
    indexed: dict[int, tools.ToolResult] = {}
    read_pairs: list[tuple[tools.ToolCall, tools.ToolResult]] = []
    n_read = sum(1 for c in calls if c.tool_name == "read")
    combine_reads = not getattr(event_handler, "single_line_tools", False)

    for idx, call in enumerate(calls):
        if call.tool_name == "read" and combine_reads and n_read >= 2:
            fn = partial(
                _execute_single_safe,
                call,
                event_handler,
                subtitle=subtitle,
                subtitle_factory=subtitle_factory,
                suppress_display=True,
            )
        else:
            fn = partial(
                _execute_single_safe,
                call,
                event_handler,
                subtitle=subtitle,
                subtitle_factory=subtitle_factory,
            )
        context = contextvars.copy_context()
        cancel_scope = CancellationScope()
        if agent_ctx is not None:
            agent_ctx.tool_cancel_scope = cancel_scope

        def _run_tool(
            context=context,
            cancel_scope=cancel_scope,
            fn=fn,
        ):
            def _invoke():
                with use_cancellation_scope(cancel_scope):
                    return fn()

            return context.run(_invoke)

        try:
            result = await loop.run_in_executor(None, _run_tool)
        finally:
            if agent_ctx is not None and agent_ctx.tool_cancel_scope is cancel_scope:
                agent_ctx.tool_cancel_scope = None

        # _execute_single() завершает Working.begin/finish_call внутри worker
        # thread. После возврата в основной event loop принудительно просим
        # свежий кадр и отдаём один тик loop'у: так result одного инструмента
        # успевает зафиксироваться в scrollback/dynamic ДО старта следующего.
        working = getattr(agent_ctx, "working_round", None) if agent_ctx is not None else None
        if working is not None:
            working.invalidate()
        else:
            try:
                from ui.shell import get_shell

                shell = get_shell()
                if shell is not None:
                    shell.invalidate()
            except Exception:
                logger.debug("tool post-exec UI invalidate failed", exc_info=True)
        await asyncio.sleep(0)

        if call.tool_name == "read" and combine_reads and n_read >= 2:
            read_pairs.append((call, result))
        indexed[idx] = result

    # Показываем все read одним компактным блоком
    if read_pairs:
        from agent.display import show_read_combined

        show_read_combined(read_pairs)

    results = [indexed[i] for i in range(len(calls))]

    # Track in turn stats
    if agent_ctx and hasattr(agent_ctx, "_turn_stats") and agent_ctx._turn_stats:
        for r in results:
            agent_ctx._turn_stats.add_tool_call(r.elapsed or 0.0)

    return results
