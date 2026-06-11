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

# Человекочитаемые названия инструментов для компактного заголовка (как в CLI).
_TOOL_LABEL = {
    "read_files": "Read", "read_file": "Read", "write_file": "Write",
    "patch_file": "Patch", "create_file": "Create", "delete_file": "Delete",
    "rename_file": "Rename", "copy_file": "Copy", "move_file": "Move",
    "ls": "List", "tree": "Tree", "mkdir": "Mkdir", "rmdir": "Rmdir",
    "find_files": "Find", "grep_files": "Grep", "shell": "Shell",
    "web_search": "Web", "skill": "Skill", "subagent": "Subagent",
    "poll": "Poll", "ssh": "SSH", "create_docx": "Docx", "docx_screenshot": "DocxShot",
    "lsp_definition": "Def", "lsp_references": "Refs",
    "lsp_hover": "Hover", "lsp_diagnostics": "Diagnostics",
}

# Краткие человекочитаемые названия аргумента для заголовка tool-вызова.
_TOOL_ARG_KEY = {
    "read_files": "path", "read_file": "path", "write_file": "path",
    "patch_file": "path", "create_file": "path", "delete_file": "path",
    "rename_file": "path", "copy_file": "src", "move_file": "src",
    "ls": "path", "tree": "path", "mkdir": "path", "rmdir": "path",
    "find_files": "pattern", "grep_files": "pattern",
    "shell": "command", "web_search": "query", "skill": "name",
}


# Регексы для извлечения статистики строк из output файловых инструментов.
# Инструменты уже считают строки — переиспользуем их цифры, не пересчитываем.
_RE_PATCH_CHANGED = re.compile(r"(\d+)\s+changed")
_RE_PATCH_ADDED = re.compile(r"\+(\d+)\s+added")
_RE_PATCH_REMOVED = re.compile(r"-(\d+)\s+removed")
_RE_WRITE_LINES = re.compile(r"(\d+)\s+lines")


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
    tail = limit - head - 20
    return f"{text[:head]}\n…(truncated {len(text) - limit} chars)…\n{text[-tail:]}"


def _html_escape(text: str) -> str:
    import html
    return html.escape(text, quote=False)


def _arg_hint(call: tools.ToolCall) -> str:
    """Короткая подсказка по главному аргументу инструмента (как arg в CLI-заголовке)."""
    args = call.args or {}
    key = _TOOL_ARG_KEY.get(call.tool_name)
    val = args.get(key) if key else None
    if val is None:
        for k in ("path", "command", "query", "pattern", "name", "url"):
            if k in args:
                val = args[k]
                break
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        if not val:
            return ""
        first = str(val[0])
        extra = f" +{len(val) - 1}" if len(val) > 1 else ""
        val = f"{first}{extra}"
    sval = str(val).strip()
    # Многострочное (shell-команда) — только первая строка.
    if "\n" in sval:
        sval = sval.split("\n", 1)[0] + " …"
    return _trunc(sval, 100)


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

        name = result.name
        ok = result.status == "ok"
        icon = "✓" if ok else "✗"
        label = _TOOL_LABEL.get(name, name)
        elapsed = f" · {result.elapsed:.1f}s" if result.elapsed else ""
        hint = _arg_hint(call) if call else ""

        # Статистика строк для файловых операций (~changed +added -removed / N lines).
        stats = _line_stats(result) if ok else ""

        # Компактная строка как в CLI: `✓ Label(arg) · 1.2s · ~3 +5 -2`
        if hint:
            head = f"{icon} <b>{_html_escape(label)}</b>(<code>{_html_escape(hint)}</code>){elapsed}{stats}"
        else:
            head = f"{icon} <b>{_html_escape(label)}</b>{elapsed}{stats}"

        # Успех — только заголовок, без содержимого.
        if ok:
            self._send(head)
            return

        # Ошибка — заголовок + первая значимая строка вывода (одной строкой).
        head += f" · exit={result.exit_code}"
        err = (result.output or "").strip()
        if err:
            first = err.split("\n", 1)[0].strip()
            self._send(f"{head}\n<i>{_html_escape(_trunc(first, 200))}</i>")
        else:
            self._send(head)

    def on_plan_update(self, plan: Plan) -> None:
        self._base.on_plan_update(plan)
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

    def mirror_assistant(self, text: str) -> None:
        if not text or not text.strip():
            return
        self._send(f"🤖 <b>Assistant</b>\n{md_to_tg_html(_trunc(text, MAX_OUTPUT))}")

    def mirror_reasoning(self, text: str) -> None:
        if not text or not text.strip():
            return
        from config.i18n import t as _i18n
        self._send(f"💭 <i>{_i18n('ui.thinking')}</i>\n<blockquote>{_html_escape(_trunc(text, MAX_OUTPUT))}</blockquote>")