"""TelegramEventHandler — декоратор поверх RichEventHandler, дублирует события в TG.

Не заменяет существующий handler — оборачивает его, чтобы UI терминала
работал как раньше, а в TG шли те же события в человекочитаемом виде.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import tools
from planner import Plan, StepStatus

from agent.events import AgentEventHandler
from agent.tg_format import md_to_tg_html

logger = logging.getLogger(__name__)

# Лимиты для зеркалирования (чтобы не зафлудить TG)
MAX_OUTPUT = 1500
# Лимиты для расширенного tool-формата (вызов + вывод в блоках кода).
_TG_INVOCATION_LIMIT = 600
_TG_TOOL_OUT_LIMIT = 1200

# Регексы для извлечения статистики строк из output файловых инструментов.
# Инструменты уже считают строки — переиспользуем их цифры, не пересчитываем.
_RE_PATCH_CHANGED = re.compile(r"(\d+)\s+changed")
_RE_PATCH_ADDED = re.compile(r"\+(\d+)\s+added")
_RE_PATCH_REMOVED = re.compile(r"-(\d+)\s+removed")
_RE_WRITE_LINES = re.compile(r"(\d+)\s+lines")

def _tool_label_with_emoji(tool_name: str) -> str:
    """`📖 Read` — те же эмодзи и подписи, что в CLI (из config/ui.py).

    MCP-инструменты (mcp__server__tool) тоже получают свой display через
    ui.mcp_display, как в терминале.
    """
    from config.ui import ui

    if tool_name.startswith("mcp__"):
        rest = tool_name[5:]
        if "__" in rest:
            server, tname = rest.split("__", 1)
            info = ui.mcp_display(server, tname)
            emoji = (info.get("emoji") or "🔌").strip()
            label = info.get("label") or f"{server}.{tname}"
            return f"{emoji} {label}".strip()

    entry = ui.tool(tool_name)
    emoji = (entry.get("emoji") or "").strip()
    label = entry.get("label") or tool_name
    return f"{emoji} {label}".strip()

def _line_stats(result: tools.ToolResult) -> str:
    """Компактная сводка строк по файловой операции (без содержимого).

    Цифры берём из result.output — write/create/patch их уже посчитали.
    Возвращает суффикс вида ` · ~3 +5 -2` (patch) или ` · 12 lines` (write/create)
    либо пустую строку, если статистики нет.
    """
    out = result.output or ""
    name = result.name
    if name == "patch_file":
        changed = _RE_PATCH_CHANGED.search(out)
        added = _RE_PATCH_ADDED.search(out)
        removed = _RE_PATCH_REMOVED.search(out)
        parts = []
        if changed:
            parts.append(f"~{changed.group(1)}")
        if added:
            parts.append(f"+{added.group(1)}")
        if removed:
            parts.append(f"-{removed.group(1)}")
        return f" · {' '.join(parts)}" if parts else ""
    if name in ("write_file", "create_file"):
        m = _RE_WRITE_LINES.search(out)
        return f" · {m.group(1)} lines" if m else ""
    return ""

def _trunc(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = max(0, limit - head - 20)
    # tail==0 → text[-0:] вернул бы ВСЮ строку, поэтому хвост берём явно.
    tail_str = text[len(text) - tail:] if tail > 0 else ""
    return f"{text[:head]}\n…(truncated {len(text) - limit} chars)…\n{tail_str}"

def _html_escape(text: str) -> str:
    import html
    return html.escape(text, quote=False)

def _format_invocation(call: tools.ToolCall) -> str:
    """Человекочитаемое представление вызова инструмента для блока кода в TG.

    shell → сама команда; контентные (write/create/patch) → путь + краткое тело;
    прочее → ключевые аргументы построчно (key: value), длинные значения режутся.
    """
    name = call.tool_name
    args = call.args or {}

    if name == "shell":
        cmd = str(args.get("command") or "").strip()
        return f"$ {cmd}" if cmd else name

    lines: list[str] = [name]
    for k, v in args.items():
        if isinstance(v, (list, tuple)):
            sval = ", ".join(str(x) for x in v)
        else:
            sval = str(v)
        sval = sval.strip()
        if "\n" in sval:
            # Многострочное тело (content/patch) — показываем целиком, но обрежем позже.
            lines.append(f"{k}:")
            lines.append(sval)
        else:
            lines.append(f"  {k}: {_trunc(sval, 200)}")
    return "\n".join(lines)

class TelegramEventHandler:
    """Зеркало событий агента в Telegram. Делегирует базовому handler'у."""

    def __init__(self, base: AgentEventHandler):
        self._base = base
        self._pending_call: Optional[tools.ToolCall] = None

    def _send(self, text: str) -> None:
        try:
            from apis.telegram import get_bridge
            bridge = get_bridge()
            if bridge.is_running:
                bridge.send(text)
        except Exception as e:
            logger.debug("tg mirror send failed: %s", e, exc_info=True)

    def on_tool_start(self, call: tools.ToolCall, subtitle: str = "") -> None:
        self._base.on_tool_start(call, subtitle)
        self._pending_call = call
        # В TG показываем только результат — стартовое сообщение с аргументами не нужно.

    def on_tool_result(self, result: tools.ToolResult) -> None:
        call = self._pending_call
        self._base.on_tool_result(result)
        self._pending_call = None

        import config as _cfg

        name = result.name
        ok = result.status == "ok"
        icon = "✓" if ok else "✗"
        label = _tool_label_with_emoji(name)
        elapsed = f" · {result.elapsed:.1f}s" if result.elapsed else ""
        stats = _line_stats(result) if ok else ""

        # Заголовок: `✓ Label · 1.2s · ~3 +5 -2`
        head = f"{icon} <b>{_html_escape(label)}</b>{elapsed}{stats}"
        if not ok:
            head += f" · exit={result.exit_code}"

        # Старый компактный формат (только заголовок) — если расширенный
        # вывод выключен в /tg.
        if not _cfg.get_telegram_tool_io():
            if ok:
                self._send(head)
                return
            err = (result.output or "").strip()
            if err:
                first = err.split("\n", 1)[0].strip()
                self._send(f"{head}\n<i>{_html_escape(_trunc(first, 200))}</i>")
            else:
                self._send(head)
            return

        # Расширенный формат: заголовок + блок вызова + блок вывода.
        msg = head
        invocation = _format_invocation(call) if call else ""
        if invocation:
            msg += f"\n<pre>{_html_escape(_trunc(invocation, _TG_INVOCATION_LIMIT))}</pre>"
        out = (result.output or "").strip()
        if out:
            msg += f"\n<pre>{_html_escape(_trunc(out, _TG_TOOL_OUT_LIMIT))}</pre>"
        self._send(msg)

    def on_plan_update(
        self,
        plan: Plan,
        action: str = "",
        focus_index: int | None = None,
    ) -> None:
        self._base.on_plan_update(plan, action=action, focus_index=focus_index)
        if not plan or not plan.steps:
            return
        icons = {
            StepStatus.PENDING: "⏳",
            StepStatus.IN_PROGRESS: "▶️",
            StepStatus.DONE: "✅",
            StepStatus.SKIPPED: "⏭",
        }
        lines = [f"📋 <b>Plan</b> [{plan.progress_str}]"]
        if plan.goal:
            lines.append(f"<i>{_html_escape(_trunc(plan.goal, 200))}</i>")
        for i, step in enumerate(plan.steps):
            icon = icons.get(step.status, "•")
            line = f"{icon} <b>{i}.</b> {_html_escape(_trunc(step.title, 200))}"
            if step.notes:
                line += f" — <i>{_html_escape(_trunc(step.notes, 100))}</i>"
            lines.append(line)
        self._send("\n".join(lines))

    def on_status(self, message: str, level: str = "info") -> None:
        self._base.on_status(message, level)
        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅"}.get(level, "•")
        self._send(f"{emoji} {_html_escape(_trunc(message, 500))}")

    def on_subagent_start(
        self, index: int, total: int, mode: str, prompt: str,
        model_label: str = "",
    ) -> None:
        self._base.on_subagent_start(index, total, mode, prompt, model_label=model_label)
        model_suffix = f" · {_html_escape(model_label)}" if model_label else ""
        self._send(
            f"🤖 <b>Subagent {index + 1}/{total}</b> [{_html_escape(mode)}]{model_suffix}\n"
            f"<i>{_html_escape(_trunc(prompt, 400))}</i>"
        )

    def on_subagent_status(self, index: int, message: str) -> None:
        self._base.on_subagent_status(index, message)
        self._send(f"🤖 <b>Subagent {index + 1}</b>: {_html_escape(_trunc(message, 300))}")

    def on_subagent_done(self, index: int, result=None) -> None:
        self._base.on_subagent_done(index, result)
        if result is None:
            return
        if getattr(result, "error", None):
            self._send(
                f"❌ <b>Subagent {index + 1}</b> FAILED\n"
                f"<pre>{_html_escape(_trunc(str(result.error), MAX_OUTPUT))}</pre>"
            )
        else:
            resp = getattr(result, "response", "") or ""
            self._send(
                f"✅ <b>Subagent {index + 1}</b> done\n"
                f"{md_to_tg_html(_trunc(resp, MAX_OUTPUT))}"
            )

    # дополнительные методы зеркалирования (не из протокола)

    def mirror_user(self, text: str) -> None:
        self._send(f"👤 <b>User</b>\n{_html_escape(_trunc(text, MAX_OUTPUT))}")

    def mirror_assistant(self, text: str, cancelled: bool = False) -> None:
        import config as _cfg
        body = (text or "").strip()
        if not body and not cancelled:
            return
        prefix = "⏹ <i>[Interrupted]</i>\n" if cancelled else ""
        rendered = md_to_tg_html(_trunc(body, MAX_OUTPUT)) if body else "[Interrupted]"
        if _cfg.get_telegram_assistant_header():
            self._send(f"🤖 <b>Assistant</b>\n{prefix}{rendered}")
        else:
            self._send(f"{prefix}{rendered}")

    def mirror_reasoning(self, text: str) -> None:
        if not text or not text.strip():
            return
        from config.i18n import t as _i18n
        self._send(f"💭 <i>{_i18n('ui.thinking')}</i>\n<blockquote>{_html_escape(_trunc(text, MAX_OUTPUT))}</blockquote>")