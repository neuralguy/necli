"""Replay RenderStore → terminal. Перепечатывает всю историю в текущем compact_mode.

Весь replay собирается в ОДИН буфер и отдаётся Shell'у одним `print_static_raw`.
Причина: экраном владеет Application, а каждый вывод в scrollback — это отдельный
`run_in_terminal` (снять рамку → напечатать → вернуть рамку). Печатать историю
поэлементно значило бы дёргать рамку столько раз, сколько в сессии сообщений, и
между этими вызовами в тот же терминал мог бы вклиниться идущий ответ агента.
Сырой ANSI (эхо ввода, welcome-капчур) пишется в тот же буфер, поэтому порядок
строк гарантирован.
"""

from __future__ import annotations

import io
import sys

from rich.console import Console
from rich.text import Text

from agent.render_store import (
    RenderStore,
    deserialize_tool_call,
    deserialize_tool_result,
)


class _StaticWriter:
    """file-like для Rich: копит строку и отдаёт её статическим каналом.

    Нужен, чтобы в модуле не осталось ни одной прямой записи в stdout: пока
    экраном владеет Application, такая запись садится посреди его кадра. Rich
    после каждого print зовёт flush, поэтому одна печать = один вывод.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, text: str) -> int:
        self._parts.append(text)
        return len(text)

    def flush(self) -> None:
        if not self._parts:
            return
        text, self._parts = "".join(self._parts), []
        _raw(text)

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return sys.__stdout__.fileno()


console = Console(file=_StaticWriter(), force_terminal=True)

# Сюда commands/helpers._print_welcome сохраняет параметры для replay.
# Храним готовый ANSI-капчур (быстрый replay без перерендера панели).
_LAST_WELCOME_ARGS: dict | None = None
_LAST_WELCOME_CAPTURE: str | None = None

#: Активный буфер replay: (Console в StringIO, сам StringIO). Пока он есть, весь
#: вывод — и Rich, и сырой ANSI — уходит туда, а не в терминал.
_SINK: tuple[Console, io.StringIO] | None = None


def _out() -> Console:
    """Консоль для текущего вывода: буфер replay либо статический канал."""
    return _SINK[0] if _SINK is not None else console


def _raw(text: str) -> None:
    """Сырая ANSI-строка: в буфер replay, иначе в scrollback через Shell."""
    if not text:
        return
    if _SINK is not None:
        _SINK[1].write(text)
        return
    from ui.shell import get_shell

    shell = get_shell()
    if shell is not None:
        shell.print_static_raw(text)
        return
    try:
        sys.__stdout__.write(text)
        sys.__stdout__.flush()
    except Exception:
        from logger import logger

        logger.opt(exception=True).debug("raw write failed")


def _term_width() -> int:
    try:
        import os as _os

        return _os.get_terminal_size().columns
    except Exception:
        return 80


def _open_sink():
    """Ставит буфер replay и возвращает функцию, которая его закрывает и сливает.

    Консоль буфера настраивается как терминальная и по ширине терминала: иначе
    Rich решил бы, что пишет в файл, и выкинул бы цвета и перенос по 80 колонкам.
    """
    global _SINK
    import agent.display as _ad

    buf = io.StringIO()
    con = Console(
        file=buf,
        force_terminal=True,
        width=_term_width(),
        color_system=_replay_color_system(),
        soft_wrap=False,
    )
    saved_sink, saved_capture = _SINK, _ad.get_static_capture()
    _SINK = (con, buf)
    # Рендеры в agent/display печатают через print_static — перенаправляем и их,
    # иначе шапки инструментов ушли бы в терминал отдельным run_in_terminal и
    # разъехались с остальной историей.
    _ad.set_static_capture(con)

    def close() -> None:
        global _SINK
        _SINK = saved_sink
        _ad.set_static_capture(saved_capture)
        _raw(buf.getvalue())

    return close


def _replay_color_system() -> str:
    """Та же глубина цвета, что у Shell: иначе история и рамка разъедутся в тонах."""
    try:
        from ui.shell import color_system_for, detect_color_depth, get_shell

        shell = get_shell()
        if shell is not None:
            return shell.bridge.color_system
        return color_system_for(detect_color_depth())
    except Exception:
        return "truecolor"


def clear_terminal() -> None:
    """Жёстко очищает экран и скролл-буфер.

    Через тот же статический канал, что и сам replay: прямой os.system("clear")
    писал бы в терминал мимо Application, и рамка осталась бы на экране
    «призраком» — prompt_toolkit не знал бы, что её стёрли.
    """
    _raw("\033[3J\033[H\033[2J")


def replay(store: RenderStore, *, expand: bool = False) -> None:
    """expand=True → compact-preview раскрывается полностью, False → свёрнутый вид."""
    if not store.items:
        return
    from agent.display import set_expanded_preview, set_replay_active

    set_replay_active(True)
    # Флаг persistent: остаётся между replay'ями (toggle через Ctrl+O).
    set_expanded_preview(bool(expand))
    close = _open_sink()
    try:
        # Каждый самостоятельный элемент ведёт свой separator. Исключение —
        # tool сразу после assistant: в live они тоже идут вплотную. Два
        # соседних tool-блока разделяются одной пустой строкой.
        _replay_inner(store)
        # Хвостовая пустая перед prompt'ом (элементы ведут, а не замыкают).
        _out().print()
    finally:
        set_replay_active(False)
        close()


def _replay_inner(store: RenderStore) -> None:
    from agent.display import render_md_panel, show_command, show_tool_combined

    _replay_welcome()
    previous_kind = ""
    for item in store.items:
        try:
            _replay_item(
                item,
                show_tool_combined,
                show_command,
                render_md_panel,
                previous_kind=previous_kind,
            )
            previous_kind = item.kind
        except Exception:
            from logger import logger

            logger.opt(exception=True).debug("replay item failed: kind={}", item.kind)


def _replay_item(
    item,
    show_tool_combined,
    show_command,
    render_md_panel,
    *,
    previous_kind: str = "",
) -> None:
    kind = item.kind
    p = item.payload or {}

    if kind == "user":
        text = p.get("text", "")
        if text:
            _out().print()
            _print_user_line(text, status=p.get("status", ""), time_str=p.get("time", ""))
        return

    if kind == "assistant":
        text = p.get("text", "")
        if not text.strip():
            return
        _out().print()
        _out().print(
            render_md_panel(
                text,
                subtitle=p.get("subtitle", ""),
                message_num=int(p.get("message_num") or 0),
            )
        )
        return

    if kind == "tool":
        call_d = p.get("call")
        result_d = p.get("result")
        subtitle = p.get("subtitle", "")
        if not call_d:
            return
        # Каждый блок в replay отделён ровно одной пустой строкой — как в live.
        _out().print()
        call = deserialize_tool_call(call_d)
        result = deserialize_tool_result(result_d) if result_d else None
        if result is None:
            show_command(call.command, tool_name=call.tool_name, args=call.args, subtitle=subtitle)
        else:
            show_tool_combined(call, result, subtitle=subtitle)
        return

    if kind == "command_only":
        call_d = p.get("call")
        if not call_d:
            return
        _out().print()
        call = deserialize_tool_call(call_d)
        show_command(
            call.command, tool_name=call.tool_name, args=call.args, subtitle=p.get("subtitle", "")
        )
        return

    if kind == "think":
        steps = p.get("steps") or []
        _replay_think(steps)
        return

    if kind == "reasoning":
        text = p.get("text") or ""
        _replay_reasoning(text)
        return

    if kind == "plan":
        plan = p.get("plan") or {}
        _replay_plan(plan, action=p.get("action", ""), focus_index=p.get("focus_index"))
        return

    if kind == "worked":
        label = p.get("label", "")
        if label:
            _out().print()
            _out().print(f"[grey50]⏱ {label}[/grey50]")
        return

    if kind == "working":
        from agent.working import _finished_header
        from config.themes import t as theme
        from ui.formatting import format_tokens

        elapsed = float(p.get("elapsed", 0.0) or 0.0)
        calls = int(p.get("calls", 0) or 0)
        ai_requests = int(p.get("ai_requests", 0) or 0)
        outcome = str(p.get("outcome") or "worked")
        has_split_tokens = "input_tokens" in p or "output_tokens" in p
        input_tokens = int(p.get("input_tokens", 0) or 0)
        output_tokens = int(
            p.get("output_tokens", 0) or (p.get("tokens", 0) if not has_split_tokens else 0)
        )
        output_prefix = "~" if p.get("output_estimated") or not has_split_tokens else ""
        _out().print()
        header = _finished_header(elapsed, outcome)
        details = Text("   ⎿  ", style=theme("dim_text"))
        details.append(f"{ai_requests} ⟳", style=theme("fg_primary"))
        details.append(" · ", style="dim")
        details.append(f"{calls} 🛠", style=theme("fg_primary"))
        details.append(" · ", style="dim")
        details.append(f"↑{format_tokens(input_tokens)}", style=theme("fg_primary"))
        details.append(" ", style="dim")
        details.append(f"↓{output_prefix}{format_tokens(output_tokens)}", style=theme("fg_primary"))
        _out().print(header)
        _out().print(details)
        return

    if kind == "raw_console":
        cmd_text = p.get("command", "")
        output = p.get("output", "")
        if cmd_text:
            from config.themes import t as theme

            _out().print()
            line = Text()
            line.append("\u2500 ", style=theme("muted"))
            line.append(cmd_text, style=f"bold {theme('accent')}")
            _out().print(line)
        if output:
            # Сырой ANSI как есть — в тот же поток, что и всё остальное.
            _raw(output if output.endswith("\n") else output + "\n")
        return


def print_session_history(necli_session, *, max_messages: int = 20) -> None:
    """Печатает последние max_messages сообщений сессии в терминал.

    Используется при смене сессии (/sessions) и при старте с --resume, чтобы
    пользователь сразу видел недавнюю историю диалога. Рендер тот же, что в
    live: user-строка, assistant-панель (с мыслями), tool-вызовы из :::call
    блоков вместе с их результатами из tool_result-сообщений.
    """
    messages = getattr(necli_session, "messages", None) or []
    if not messages:
        return

    # Берём хвост из max_messages не-system сообщений; ведущие/служебные
    # system-сообщения (compressed-мета и т.п.) в визуальную историю не идут.
    visible = [
        m for m in messages if m.role in ("user", "assistant", "tool_result", "worked", "tool_call")
    ]
    if not visible:
        return
    if max_messages > 0:
        visible = visible[-max_messages:]
        # Не начинаем с tool_result — он принадлежит отрезанному assistant.
        if visible and visible[0].role == "tool_result":
            visible = visible[1:]

    from agent.display import (
        render_md_panel,
        set_expanded_preview,
        set_replay_active,
        show_command,
        show_tool_combined,
    )
    from tools.models import ToolCall
    from tools.parser import parse_tool_calls, strip_tool_calls

    set_replay_active(True)
    set_expanded_preview(False)
    close = _open_sink()
    try:
        pending_calls: list[ToolCall] = []
        for msg in visible:
            content = msg.content or ""
            if not content.strip():
                continue
            if msg.role == "user":
                _flush_pending(pending_calls, show_command)
                _out().print()
                _print_user_line(content)
                continue
            if msg.role == "assistant":
                # Мысли (think) — отдельным блоком перед текстом.
                if msg.thoughts:
                    _replay_think(msg.thoughts)
                # Raw reasoning (reasoning_content) — отдельной панелью с пометкой raw.
                if getattr(msg, "reasoning", ""):
                    _replay_reasoning(msg.reasoning)
                clean = strip_tool_calls(content)
                if clean.strip():
                    _out().print()
                    _out().print(render_md_panel(clean))
                pending_calls.extend(parse_tool_calls(content))
                continue
            if msg.role == "worked":
                _render_worked(content)
                continue
            if msg.role == "tool_call":
                # Native: вызовы хранятся отдельным JSON-сообщением. Копим их
                # в pending_calls, чтобы следующий tool_result связал их с выводом.
                pending_calls.extend(_parse_tool_calls_json(content))
                continue
            # tool_result: парсим результаты и связываем с вызовами по индексу.
            results = _parse_tool_results(content)
            for res in results:
                call = None
                if pending_calls:
                    call = pending_calls.pop(0)
                if call is not None:
                    _out().print()
                    show_tool_combined(call, res)
                else:
                    show_command(
                        res.command or res.name,
                        tool_name=res.name,
                        args={"exit_code": res.exit_code},
                    )
        _flush_pending(pending_calls, show_command)
        _out().print()
    finally:
        set_replay_active(False)
        close()


def _flush_pending(pending_calls: list, show_command) -> None:
    """Дорисовывает tool-вызовы, у которых не было tool_result (обрыв/прерывание)."""
    for call in pending_calls:
        _out().print()
        show_command(call.command, tool_name=call.tool_name, args=call.args)
    pending_calls.clear()


def _parse_tool_results(text: str) -> list:
    """Разбирает tool_result-сообщение в ToolResult'ы.

    Поддерживает два формата:
      - <runtime_tool_results> XML (fenced-режим);
      - '$ cmd [exit N]\\n<output>' (native-режим, см. _split_tool_result_segments).
    """
    import re

    from tools.models import ToolResult

    results: list[ToolResult] = []
    for m in re.finditer(
        r"<result\s+([^>]*)>\s*<!\[CDATA\[(.*?)\]\]>\s*</result>",
        text,
        flags=re.DOTALL,
    ):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        output = m.group(2).strip("\n")
        exit_code = 0
        try:
            exit_code = int(attrs.get("exit_code", "0"))
        except (TypeError, ValueError):
            exit_code = 0
        name = attrs.get("tool", "tool")
        results.append(
            ToolResult(
                name=name,
                status="ok" if exit_code == 0 else "error",
                output=output,
                exit_code=exit_code,
                command=attrs.get("command", name),
            )
        )
    if results:
        return results

    # Формат '$ cmd [exit N]\\n<output>' — режем по заголовкам '$ '.
    segments: list[str] = []
    cur: list[str] = []
    for ln in text.split("\n"):
        if ln.startswith("$ ") and cur:
            segments.append("\n".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        segments.append("\n".join(cur))

    known_tools = {
        "shell",
        "read",
        "grep",
        "patch_file",
        "create_file",
        "docx",
        "pptx",
        "poll",
        "skill",
        "subagent",
        "web_search",
        "web_fetch",
        "image_search",
        "expand_tool_result",
        "memory",
        "lsp_references",
        "lsp_diagnostics",
    }
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        first, _, output = seg.partition("\n")
        m2 = re.match(r"\$ (.+?)(?: \[exit (-?\d+)\])?$", first)
        cmd = m2.group(1) if m2 else first[2:]
        exit_code = int(m2.group(2)) if m2 and m2.group(2) else 0
        first_word = cmd.split()[0] if cmd.split() else ""
        name = first_word if first_word in known_tools else "shell"
        results.append(
            ToolResult(
                name=name,
                status="ok" if exit_code == 0 else "error",
                output=output.strip("\n"),
                exit_code=exit_code,
                command=cmd,
            )
        )
    return results


def _parse_tool_calls_json(content: str) -> list:
    """Разбирает JSON-сообщение роли tool_call (native-режим) в ToolCall'ы."""
    import json

    from tools.models import ToolCall

    try:
        data = json.loads(content)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    calls: list[ToolCall] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "shell")
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        command = str(args.get("command") or name) if name == "shell" else name
        calls.append(ToolCall(command=command, tool_name=name, args=args, raw=""))
    return calls


def _render_worked(content: str) -> None:
    """Рендерит сводку Working-раунда (роль "worked") как в live: заголовок + детали."""
    try:
        import json

        p = json.loads(content)
    except Exception:
        p = {}
    if not isinstance(p, dict):
        p = {}

    from agent.working import _finished_header
    from config.themes import t as theme
    from ui.formatting import format_tokens

    elapsed = float(p.get("elapsed", 0.0) or 0.0)
    calls = int(p.get("calls", 0) or 0)
    ai_requests = int(p.get("ai_requests", 0) or 0)
    outcome = str(p.get("outcome") or "worked")
    has_split_tokens = "input_tokens" in p or "output_tokens" in p
    input_tokens = int(p.get("input_tokens", 0) or 0)
    output_tokens = int(
        p.get("output_tokens", 0) or (p.get("tokens", 0) if not has_split_tokens else 0)
    )
    output_prefix = "~" if p.get("output_estimated") or not has_split_tokens else ""

    _out().print()
    header = _finished_header(elapsed, outcome)
    details = Text("   ⎿  ", style=theme("dim_text"))
    details.append(f"{ai_requests} 🔄", style=theme("fg_primary"))
    details.append(" · ", style="dim")
    details.append(f"{calls} 🛠", style=theme("fg_primary"))
    details.append(" · ", style="dim")
    details.append(f"↑{format_tokens(input_tokens)}", style=theme("fg_primary"))
    details.append(" ", style="dim")
    details.append(f"↓{output_prefix}{format_tokens(output_tokens)}", style=theme("fg_primary"))
    _out().print(header)
    _out().print(details)


def _replay_welcome() -> None:
    """Перепечатывает welcome-панель из кэша (быстрый replay)."""
    try:
        cap = _LAST_WELCOME_CAPTURE
        if cap:
            _raw(cap if cap.endswith("\n") else cap + "\n")
            return
        args = _LAST_WELCOME_ARGS
        if not args:
            return
        # Панель welcome печатает commands/helpers своей модульной консолью —
        # подменяем её на консоль буфера, иначе шапка ушла бы в терминал
        # отдельным потоком и оказалась НИЖЕ истории.
        import commands.helpers as _h
        from session import storage as _sst

        sid = args.get("session_id", "")
        sess = None
        try:
            sess = _sst.Session.load(sid) if sid else None
        except Exception:
            sess = None
        if sess is None:
            # Fallback: создадим минимальный stub
            class _Stub:
                id = sid
                title = ""
                message_count = 0
                models_used = []  # noqa: RUF012
                raw_input_tokens = 0
                output_tokens = 0
                total_cost = 0.0

            sess = _Stub()
        _saved_h = _h.console
        _h.console = _out()
        try:
            _h._print_welcome(
                args.get("model", ""),
                sess,
                workdir=args.get("workdir", "."),
                n_lsp=int(args.get("n_lsp", 0) or 0),
                n_mcp=int(args.get("n_mcp", 0) or 0),
                mcp_tools=int(args.get("mcp_tools", 0) or 0),
                tg_info=args.get("tg_info", "") or "",
            )
        finally:
            _h.console = _saved_h
    except Exception:
        from logger import logger

        logger.opt(exception=True).debug("replay welcome failed")


def _print_user_line(text: str, status: str = "", time_str: str = "") -> None:
    from wcwidth import wcswidth

    from config.themes import t as theme

    try:
        import os as _os

        w = _os.get_terminal_size().columns
    except Exception:
        w = 80

    def _vw(s: str) -> int:
        n = wcswidth(s)
        return n if n >= 0 else len(s)

    # Разделитель: статус-строка этого turn'а (как при реальном вводе), либо
    # голая линия если статус не сохранён (старые сессии). Печатаем через ту
    # же rich-console, что и остальной replay (НЕ prompt_toolkit
    # print_formatted_text — он буферизуется отдельно и вываливается в конце).
    if status:
        try:
            from ui.prompt import InputPrompt

            _ip = InputPrompt.__new__(InputPrompt)
            frags = _ip._make_separator_fragments(status)
            _cls_style = {
                "class:separator": theme("muted"),
                "class:status-text": f"bold {theme('fg_primary')}",
                "class:bar-filled": theme("accent"),
                "class:bar-empty": theme("muted"),
            }
            line = Text()
            for cls, txt in frags:
                line.append(txt, style=_cls_style.get(cls, theme("muted")))
            _out().print(line)
        except Exception:
            _out().print(Text("\u2500" * w, style=theme("muted")))
    else:
        _out().print(Text("\u2500" * w, style=theme("muted")))

    # Эхо ввода: bright white на фоне bg_code, padding на всю ширину,
    # multiline с префиксом "🚀 agent > " на первой строке — как _echo_submitted.
    mode_prefix = "🚀 agent > "
    bg = theme("bg_code")
    bg_seq = ""
    if isinstance(bg, str) and bg.startswith("#") and len(bg) == 7:
        r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        bg_seq = f"48;2;{r};{g};{b}"
    try:
        rows: list[str] = []
        for i, ln in enumerate(text.split("\n")):
            prefix = mode_prefix if i == 0 else " "
            filled = prefix + ln
            pad = max(0, w - _vw(filled))
            body = filled + " " * pad
            if bg_seq:
                rows.append(f"\033[1;97;{bg_seq}m{body}\033[0m\n")
            else:
                rows.append(f"\033[1;97m{body}\033[0m\n")
        # Серое время отправки справа снизу — как render_echo в live.
        if time_str:
            rows.append(f"\033[38;5;250m{' ' * max(0, w - _vw(time_str))}{time_str}\033[0m\n")
        _raw("".join(rows))
    except Exception:
        line = Text()
        line.append(mode_prefix, style=f"bold {theme('success')}")
        line.append(text, style="")
        _out().print(line)


def _replay_think(steps: list) -> None:
    try:
        from agent.think import ThinkLog, ThoughtStep, render_think_static
    except Exception:
        return
    log = ThinkLog(steps=[ThoughtStep(text=str(s), raw_text=str(s)) for s in steps if s])
    if not log.steps:
        return
    _out().print()
    _out().print(render_think_static(log))


def _replay_reasoning(text: str) -> None:
    text = text or ""
    if not text.strip():
        return
    try:
        from agent.stream_render import render_reasoning_panel
    except Exception:
        return
    _out().print()
    _out().print(render_reasoning_panel(text))


def _replay_plan(plan: dict, action: str = "", focus_index=None) -> None:
    try:
        from planner import Plan, PlanStep, StepStatus, render_plan_panel
    except Exception:
        return
    steps = []
    for s in plan.get("steps") or []:
        status_str = s.get("status", "pending")
        try:
            status = StepStatus(status_str)
        except Exception:
            status = StepStatus.PENDING
        steps.append(
            PlanStep(
                title=s.get("title", ""),
                status=status,
                notes=s.get("notes") or "",
            )
        )
    p = Plan(goal=plan.get("goal", ""), steps=steps)
    if not p.steps:
        return
    try:
        idx = int(focus_index) if focus_index is not None else None
    except (TypeError, ValueError):
        idx = None
    _out().print()
    _out().print(render_plan_panel(p, compact=False, focus_index=idx))
