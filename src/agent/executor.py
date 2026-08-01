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
    exec_spinner_frames,
    print_static,
)
from config.themes import t
from logger import logger
from tools.parser import MAX_TOOL_CALLS_PER_MESSAGE
from ui.shell import get_shell

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


# Инструменты, которые сами рисуют живой мультиплексный UI (своя динамическая
# зона Shell). Для них НЕЛЬЗЯ поднимать спиннер «Tool …s»: две зоны нарисовали
# бы над рамкой сразу два кадра об одном и том же. У subagent своя ветка в
# loop.py — поэтому гасим индикатор здесь по имени инструмента.
_SELF_RENDERING_TOOLS = frozenset({"subagent"})


def _make_exec_indicator(tool_name: str, args: dict, elapsed: float, frame: str) -> Text:
    """Стабильный живой индикатор без подписи команды.

    Имя инструмента и его аргументы относятся к истории выполнения, поэтому
    печатаются только итоговым статическим блоком. Если держать их здесь, то
    при дозревании аргументов заголовок меняет ширину и высоту прямо под
    открытым меню — это было главным источником мерцания. Параметры оставлены
    в сигнатуре для совместимости с тестами и внешними импортами.
    """
    del tool_name, args
    out = Text()
    out.append(f"  {frame} ", style=f"bold {t('accent')}")
    out.append("working…", style="dim")
    out.append(f"  {elapsed:.1f}s", style="dim")
    return out


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
    show_live: bool = True,
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
        prompt_input = getattr(_ctx, "prompt_input", None) if _ctx else None
        if prompt_input is not None and hasattr(prompt_input, "set_activity_status"):
            try:
                prompt_input.set_activity_status("poll")
            except Exception:
                logger.debug("poll activity status set failed", exc_info=True)
        if not _silent:
            print_static(Text(""))
        try:
            result = tools.execute_call(call)
        finally:
            if prompt_input is not None and hasattr(prompt_input, "set_activity_status"):
                try:
                    prompt_input.set_activity_status("working")
                except Exception:
                    logger.debug("poll activity status restore failed", exc_info=True)
        if not _silent:
            _show_poll_result(result)
        return result
    if event_handler is not None:
        event_handler.on_tool_start(call, subtitle=subtitle)

    t0 = time.monotonic()

    if show_live and not _silent and call.tool_name not in _SELF_RENDERING_TOOLS:
        _frames = exec_spinner_frames()

        def _exec_frame() -> str:
            # Смена кадра по времени, а не на каждый кадр рендера — плавно и медленно.
            idx = int((time.monotonic() - t0) / 0.18) % len(_frames)
            return _frames[idx]

        def _indicator() -> Text:
            return _make_exec_indicator(
                call.tool_name, call.args or {}, time.monotonic() - t0, _exec_frame(),
            )

        # Тот же callable, что уходил в Live.get_renderable, отдаём Shell'у:
        # его ticker вызывает его сам, поэтому спиннер и счётчик секунд тикают
        # без ручного refresh. Инструмент может исполняться в рабочем потоке
        # (loop.run_in_executor) — set_dynamic/clear_dynamic безопасны оттуда,
        # Application.invalidate потокобезопасен по контракту prompt_toolkit.
        sh = get_shell()
        if sh is not None:
            sh.set_dynamic(_TOOL_ZONE, _indicator)
        try:
            result = tools.execute_call(call)
        finally:
            # Аналог transient=True: кадр исчезает без следа, итоговую шапку
            # печатает show_tool_combined ниже.
            if sh is not None:
                sh.clear_dynamic(_TOOL_ZONE)
    else:
        result = tools.execute_call(call)
    result.elapsed = time.monotonic() - t0
    logger.info(
        "tool_done: {} status={} exit={} elapsed={:.2f}s",
        call.tool_name, result.status, result.exit_code, result.elapsed,
    )


    final_subtitle = subtitle
    if subtitle_factory is not None:
        try:
            final_subtitle = subtitle_factory(result)
        except Exception:
            logger.debug("subtitle_factory failed for %s", call.tool_name, exc_info=True)

    # Для контентных инструментов (write/create/patch/docx) реальная «работа» —
    # это время, пока модель СТРИМИЛА тело блока (тикает в live-индикаторе), а
    # само исполнение почти мгновенно. Поэтому в финальном статичном выводе
    # показываем это streaming-время (@@WRITE_TIME=N@@ из subtitle), иначе таймер
    # схлопывался в 0.0s. Для shell/read оставляем реальное время исполнения.
    if call.tool_name in ("create_file", "patch_file", "create_docx"):
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

    return result


def _make_overflow_results(dropped: list[tools.ToolCall]) -> list[tools.ToolResult]:
    out = []
    for call in dropped:
        out.append(tools.ToolResult(  # noqa: PERF401
            name=call.tool_name,
            status="error",
            output=(
                f"Tool call dropped: exceeded limit of "
                f"{MAX_TOOL_CALLS_PER_MESSAGE} calls per message. "
                f"Repeat this call in the next message."
            ),
            exit_code=1,
            command=call.command,
        ))
    return out


def execute_and_show(calls: list[tools.ToolCall], event_handler=None, subtitle: str = "", subtitle_factory=None) -> list[tools.ToolResult]:
    dropped = []
    if len(calls) > MAX_TOOL_CALLS_PER_MESSAGE:
        logger.warning(
            "execute_and_show: dropping {} calls (limit {})",
            len(calls) - MAX_TOOL_CALLS_PER_MESSAGE, MAX_TOOL_CALLS_PER_MESSAGE,
        )
        dropped = list(calls[MAX_TOOL_CALLS_PER_MESSAGE:])
        calls = calls[:MAX_TOOL_CALLS_PER_MESSAGE]

    # Сохраняем исходный порядок: results[idx] = result
    indexed: dict[int, tools.ToolResult] = {}
    read_pairs: list[tuple[tools.ToolCall, tools.ToolResult]] = []

    for idx, call in enumerate(calls):
        if call.tool_name == "read" and sum(1 for c in calls if c.tool_name == "read") >= 2:
            # Несколько read — без индивидуального отображения
            result = _execute_single(call, event_handler, subtitle=subtitle, subtitle_factory=subtitle_factory, suppress_display=True)
            read_pairs.append((call, result))
            indexed[idx] = result
        else:
            result = _execute_single(call, event_handler, subtitle=subtitle, subtitle_factory=subtitle_factory)
            indexed[idx] = result

    # Показываем все read одним компактным блоком
    if read_pairs:
        from agent.display import show_read_combined
        show_read_combined(read_pairs)

    results = [indexed[i] for i in range(len(calls))]
    results.extend(_make_overflow_results(dropped))
    return results


async def execute_and_show_async(
    calls: list[tools.ToolCall], event_handler=None, subtitle: str = "",
    subtitle_factory=None,
) -> list[tools.ToolResult]:
    dropped = []
    if len(calls) > MAX_TOOL_CALLS_PER_MESSAGE:
        logger.warning(
            "execute_and_show_async: dropping {} calls (limit {})",
            len(calls) - MAX_TOOL_CALLS_PER_MESSAGE, MAX_TOOL_CALLS_PER_MESSAGE,
        )
        dropped = list(calls[MAX_TOOL_CALLS_PER_MESSAGE:])
        calls = calls[:MAX_TOOL_CALLS_PER_MESSAGE]

    loop = asyncio.get_running_loop()
    # Сохраняем исходный порядок: results[idx] = result
    indexed: dict[int, tools.ToolResult] = {}
    read_pairs: list[tuple[tools.ToolCall, tools.ToolResult]] = []
    n_read = sum(1 for c in calls if c.tool_name == "read")

    for idx, call in enumerate(calls):
        if call.tool_name == "read" and n_read >= 2:
            fn = partial(_execute_single, call, event_handler, subtitle=subtitle, subtitle_factory=subtitle_factory, suppress_display=True)
        else:
            fn = partial(_execute_single, call, event_handler, subtitle=subtitle, subtitle_factory=subtitle_factory)
        ctx = contextvars.copy_context()
        result = await loop.run_in_executor(None, lambda fn=fn, ctx=ctx: ctx.run(fn))
        if call.tool_name == "read" and n_read >= 2:
            read_pairs.append((call, result))
        indexed[idx] = result

    # Показываем все read одним компактным блоком
    if read_pairs:
        from agent.display import show_read_combined
        show_read_combined(read_pairs)

    results = [indexed[i] for i in range(len(calls))]
    results.extend(_make_overflow_results(dropped))
    return results
