"""Replay RenderStore → terminal. Перепечатывает всю историю в текущем compact_mode."""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.text import Text

from agent.render_store import (
    RenderStore,
    deserialize_tool_call,
    deserialize_tool_result,
)


console = Console(file=sys.__stdout__, force_terminal=True)

# Сюда commands/helpers._print_welcome сохраняет параметры для replay.
# Храним готовый ANSI-капчур (быстрый replay без перерендера панели).
_LAST_WELCOME_ARGS: dict | None = None
_LAST_WELCOME_CAPTURE: str | None = None


def clear_terminal() -> None:
    """Жёстко очищает экран и скролл-буфер."""
    try:
        os.system("clear")
    except Exception:
        try:
            sys.__stdout__.write("\033[3J\033[H\033[2J")
            sys.__stdout__.flush()
        except Exception:
            pass


def replay(store: RenderStore, *, expand: bool = False) -> None:
    """expand=True → compact-preview раскрывается полностью, False → свёрнутый вид."""
    if not store.items:
        return
    from agent.display import set_replay_active, set_expanded_preview

    set_replay_active(True)
    # Флаг persistent: остаётся между replay'ями (toggle через Ctrl+O).
    set_expanded_preview(bool(expand))
    try:
        # Каждый элемент печатает одну пустую строку ПЕРЕД собой — ровно одна
        # пустая строка между всеми пунктами (та же leading-модель, что в live).
        _replay_inner(store)
        # Хвостовая пустая перед prompt'ом (элементы ведут, а не замыкают).
        console.print()
    finally:
        set_replay_active(False)


def _replay_inner(store: RenderStore) -> None:
    from agent.display import show_tool_combined, show_command, render_md_panel
    import agent.display as _ad

    _saved_ad = _ad.console
    _ad.console = console
    try:
        _replay_welcome()
        for item in store.items:
            try:
                _replay_item(item, show_tool_combined, show_command, render_md_panel)
            except Exception:
                from logger import logger
                logger.opt(exception=True).debug("replay item failed: kind={}", item.kind)
    finally:
        _ad.console = _saved_ad


def _replay_item(item, show_tool_combined, show_command, render_md_panel) -> None:
    kind = item.kind
    p = item.payload or {}

    if kind == "user":
        text = p.get("text", "")
        if text:
            console.print()
            _print_user_line(text, status=p.get("status", ""))
        return

    if kind == "assistant":
        text = p.get("text", "")
        if not text.strip():
            return
        console.print()
        console.print(render_md_panel(
            text,
            subtitle=p.get("subtitle", ""),
            message_num=int(p.get("message_num") or 0),
        ))
        return

    if kind == "tool":
        call_d = p.get("call")
        result_d = p.get("result")
        subtitle = p.get("subtitle", "")
        if not call_d:
            return
        call = deserialize_tool_call(call_d)
        result = deserialize_tool_result(result_d) if result_d else None
        if result is None:
            show_command(call.command, tool_name=call.tool_name,
                         args=call.args, subtitle=subtitle)
        else:
            show_tool_combined(call, result, subtitle=subtitle)
        return

    if kind == "command_only":
        call_d = p.get("call")
        if not call_d:
            return
        call = deserialize_tool_call(call_d)
        show_command(call.command, tool_name=call.tool_name,
                     args=call.args, subtitle=p.get("subtitle", ""))
        return

    if kind == "think":
        steps = p.get("steps") or []
        _replay_think(steps)
        return

    if kind == "plan":
        plan = p.get("plan") or {}
        _replay_plan(plan, action=p.get("action", ""), focus_index=p.get("focus_index"))
        return

    if kind == "worked":
        label = p.get("label", "")
        if label:
            console.print()
            console.print(f"[grey50]⏱ {label}[/grey50]")
        return

    if kind == "raw_console":
        cmd_text = p.get("command", "")
        output = p.get("output", "")
        if cmd_text:
            from config.themes import t as theme
            console.print()
            line = Text()
            line.append("\u2500 ", style=theme("muted"))
            line.append(cmd_text, style=f"bold {theme('accent')}")
            console.print(line)
        if output:
            # Печатаем сырой ANSI как есть
            try:
                import sys as _sys
                _sys.__stdout__.write(output)
                if not output.endswith("\n"):
                    _sys.__stdout__.write("\n")
                _sys.__stdout__.flush()
            except Exception:
                console.print(output, highlight=False, markup=False)
        return


def print_session_history(necli_session, *, max_messages: int = 20) -> None:
    """Печатает последние max_messages сообщений сессии в терминал.

    Используется при смене сессии (/sessions) и при старте с --resume, чтобы
    пользователь сразу видел недавнюю историю диалога. Рендер тот же, что в
    live: user-строка, assistant-панель, tool-вызовы из :::call блоков.
    tool_result-сообщения пропускаются — их вывод уже виден под tool-вызовом.
    """
    messages = getattr(necli_session, "messages", None) or []
    if not messages:
        return

    # Берём хвост из max_messages не-system сообщений; ведущие/служебные
    # system-сообщения (compressed-мета и т.п.) в визуальную историю не идут.
    visible = [m for m in messages if m.role in ("user", "assistant")]
    if not visible:
        return
    if max_messages > 0:
        visible = visible[-max_messages:]

    from agent.display import set_replay_active, set_expanded_preview, render_md_panel
    from agent.display import show_command
    import agent.display as _ad
    from tools.parser import parse_tool_calls, strip_tool_calls

    _saved_ad = _ad.console
    _ad.console = console
    set_replay_active(True)
    set_expanded_preview(False)
    try:
        for msg in visible:
            content = msg.content or ""
            if not content.strip():
                continue
            if msg.role == "user":
                console.print()
                _print_user_line(content)
                continue
            # assistant: текст + восстановленные tool-вызовы (без результатов).
            clean = strip_tool_calls(content)
            if clean.strip():
                console.print()
                console.print(render_md_panel(clean))
            for call in parse_tool_calls(content):
                show_command(call.command, tool_name=call.tool_name, args=call.args)
        console.print()
    finally:
        set_replay_active(False)
        _ad.console = _saved_ad

def _replay_welcome() -> None:
    """Перепечатывает welcome-панель из кэша (быстрый replay)."""
    try:
        cap = _LAST_WELCOME_CAPTURE
        if cap:
            import sys as _sys
            _sys.__stdout__.write(cap)
            if not cap.endswith("\n"):
                _sys.__stdout__.write("\n")
            _sys.__stdout__.flush()
            return
        args = _LAST_WELCOME_ARGS
        if not args:
            return
        # Используем тот же console (real stdout) что для replay
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
                models_used = []
                raw_input_tokens = 0
                output_tokens = 0
                total_cost = 0.0
            sess = _Stub()
        # Подменяем модульный console в helpers на наш real-stdout
        _saved_h = _h.console
        _h.console = console
        try:
            _h._print_welcome(
                args.get("model", ""), sess,
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


def _print_user_line(text: str, status: str = "") -> None:
    from config.themes import t as theme
    from wcwidth import wcswidth

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
                "class:status-text": "bold #ffffff",
                "class:bar-filled": theme("accent"),
                "class:bar-empty": theme("muted"),
            }
            line = Text()
            for cls, txt in frags:
                line.append(txt, style=_cls_style.get(cls, theme("muted")))
            console.print(line)
        except Exception:
            console.print(Text("\u2500" * w, style=theme("muted")))
    else:
        console.print(Text("\u2500" * w, style=theme("muted")))

    # Эхо ввода: bright white на фоне bg_code, padding на всю ширину,
    # multiline с префиксом "🚀 agent > " на первой строке — как _echo_submitted.
    mode_prefix = "🚀 agent > "
    bg = theme("bg_code")
    bg_seq = ""
    if isinstance(bg, str) and bg.startswith("#") and len(bg) == 7:
        r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        bg_seq = f"48;2;{r};{g};{b}"
    out = sys.__stdout__
    try:
        for i, ln in enumerate(text.split("\n")):
            prefix = mode_prefix if i == 0 else " "
            filled = prefix + ln
            pad = max(0, w - _vw(filled))
            body = filled + " " * pad
            if bg_seq:
                out.write(f"\033[1;97;{bg_seq}m{body}\033[0m\n")
            else:
                out.write(f"\033[1;97m{body}\033[0m\n")
        out.flush()
    except Exception:
        line = Text()
        line.append(mode_prefix, style=f"bold {theme('success')}")
        line.append(text, style="")
        console.print(line)


def _replay_think(steps: list) -> None:
    try:
        from agent.think import ThinkLog, ThoughtStep, render_think_static
    except Exception:
        return
    log = ThinkLog(steps=[ThoughtStep(text=str(s)) for s in steps if s])
    if not log.steps:
        return
    console.print()
    console.print(render_think_static(log))


def _replay_plan(plan: dict, action: str = "", focus_index=None) -> None:
    try:
        from agent.display import is_expanded_preview
        from planner import Plan, PlanStep, StepStatus, render_plan_panel
    except Exception:
        return
    steps = []
    for s in (plan.get("steps") or []):
        status_str = s.get("status", "pending")
        try:
            status = StepStatus(status_str)
        except Exception:
            status = StepStatus.PENDING
        steps.append(PlanStep(
            title=s.get("title", ""),
            status=status,
            notes=s.get("notes") or "",
        ))
    p = Plan(goal=plan.get("goal", ""), steps=steps)
    if not p.steps:
        return
    try:
        idx = int(focus_index) if focus_index is not None else None
    except (TypeError, ValueError):
        idx = None
    full = bool(is_expanded_preview() or action == "create" or p.is_complete or idx is None)
    console.print()
    console.print(render_plan_panel(p, compact=False, focus_index=idx, full=full))