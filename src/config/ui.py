from __future__ import annotations

import json
import logging
from typing import Any

from .paths import BASE_DIR

UI_FILE = BASE_DIR / "ui.json"

_log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "_comment": "UI customization. Edit and restart CLI. Delete file to regenerate defaults.",
    "_help": {
        "_": "JSON не поддерживает комментарии — описание ключей здесь. Эта секция игнорируется кодом.",

        "limits.max_width": "Максимальная ширина любых панелей (response, tool, subagent). Реальная ширина = min(max_width, ширина_терминала).",
        "limits.compact_preview_lines": "Сколько строк показывать в compact-превью результата инструмента (не shell). Полный вывод доступен по Ctrl+O.",
        "limits.compact_preview_lines_shell": "То же для shell-команд. Обычно меньше, т.к. вывод длинный.",
        "limits.compact_head_lines": "В compact-режиме длинный текст обрезается до head + tail строк.",
        "limits.compact_tail_lines": "Парный к compact_head_lines: сколько строк с конца.",
        "limits.panel_head_lines": "То же что compact_head_lines, но для не-compact (с рамкой) панелей.",
        "limits.panel_tail_lines": "Парный к panel_head_lines.",
        "limits.streaming_max_lines": "Сколько строк ответа модели держать видимыми в Live-стриме. Старые скроллятся вверх.",
        "limits.max_result_length": "Жёсткий лимит длины result в символах — длиннее обрезается с пометкой.",
        "limits.partial_lang_bypass_threshold": "Если стримящийся текст длиннее N символов — отключается подсветка синтаксиса (лагает Live).",
        "limits.think_snippet_max_len": "Максимальная длина одной мысли (think) в компактной строке.",

        "paddings.*": "Все padding — пара [vertical, horizontal] для Rich Panel/Syntax/Table.",
        "paddings.response_panel": "Отступы внутри панели ответа модели.",
        "paddings.tool_panel": "Отступы внутри панели инструмента (write/patch/etc).",
        "paddings.code_block": "Отступы внутри блока кода (Syntax).",
        "paddings.diff_table": "Отступы ячеек таблицы side-by-side diff.",
        "paddings.subagent_panel": "Отступы панели субагента.",
        "paddings.reasoning_panel": "Отступы Reasoning-панели (стрим thinking от модели).",
        "paddings.think_panel": "Отступы статичной панели think (после завершения).",
        "paddings.executor_panel": "Отступы панели executor (выполнение команды с прогрессом).",

        "symbols.separator_horizontal": "Символ горизонтальной линии между args и output (по умолчанию '─').",
        "symbols.tree_branch": "Префикс для не-последнего элемента tree-вывода с пробелом.",
        "symbols.tree_last": "Префикс для последнего элемента tree-вывода с пробелом.",
        "symbols.tree_branch_long": "Длинный вариант (для иерархии сообщений).",
        "symbols.tree_last_long": "Длинный last.",
        "symbols.tree_pipe": "Вертикальное продолжение в tree.",
        "symbols.tree_blank": "Пустой отступ в tree (под последним элементом).",
        "symbols.summary_prefix": "Префикс саммари-строки результата (по умолчанию '⎿  ').",
        "symbols.compact_separator_prefix": "Префикс под compact-заголовком (по умолчанию '└─').",
        "symbols.vertical_pipe": "Вертикальная разделительная палочка в diff.",
        "symbols.diff_separator": "Разделитель колонок в side-by-side diff ('  │  ').",
        "symbols.bullet_active": "Активный пункт списка / compact-заголовок ('● ').",
        "symbols.bullet_dim": "Неактивный пункт ('○ ').",
        "symbols.cursor": "Курсор в конце стримящегося текста ('▌').",
        "symbols.ellipsis": "Многоточие для усечения ('…').",
        "symbols.thinking_emoji": "Эмодзи рядом с мыслями модели ('💭').",
        "symbols.interrupt_marker": "Маркер перед 'Waiting for response' ('■ ').",

        "spinner.frames": "Кадры спиннера (рисуются по очереди). По умолчанию braille-точки.",
        "spinner.interval": "Интервал между кадрами в секундах (используется только некоторыми спиннерами).",

        "diff_colors.bg_delete": "Фон удалённой строки в diff (HEX).",
        "diff_colors.bg_add": "Фон добавленной строки.",
        "diff_colors.fg_delete": "Цвет знака '-' и текста удалённой строки.",
        "diff_colors.fg_add": "Цвет знака '+' и текста добавленной строки.",
        "diff_colors.prefix_delete": "Префикс удалённой строки ('- ' с пробелом).",
        "diff_colors.prefix_add": "Префикс добавленной строки ('+ ').",
        "diff_colors.prefix_equal": "Префикс неизменной строки (два пробела).",

        "tools.<name>": "Display для каждого инструмента: label, emoji, color_role или color.",
        "tools.<name>.label": "Название в заголовке панели.",
        "tools.<name>.emoji": "Эмодзи перед label.",
        "tools.<name>.color_role": "Семантическая роль цвета (см. config/themes.py:ROLES): accent, success, warning, error, info, magenta, purple, muted и т.д. Цвет берётся из активной темы (меняется через /theme).",
        "tools.<name>.color": "ОПЦИОНАЛЬНО. Прямой HEX ('#ff8800') или имя ('bright_red') — перебивает color_role и не зависит от темы. Удобно когда хочешь конкретный цвет для одного инструмента.",
        "tools._default": "Fallback для незнакомых инструментов.",
        "tools._mcp": "Шаблон для MCP-инструментов. В label можно использовать {server} и {tool} — подставится автоматически. Тоже поддерживает 'color'.",

        "indicators.thinking_suffix": "Текст рядом со спиннером во время ожидания ответа ('thinking…').",
        "indicators.writing_prefix": "Префикс перед именем инструмента в индикаторе ('writing ').",
        "indicators.writing_suffix": "Суффикс ('…').",
        "indicators.interrupt_text": "Основной текст индикатора прерывания.",
        "indicators.interrupt_hint": "Подсказка как прервать ('(Ctrl+C = stop)').",

        "live_stream.refresh_per_second": "Частота перерисовки Live-стрима (Гц). Выше → плавнее, но больше CPU.",
        "live_stream.reserved_lines": "Сколько строк терминала зарезервировать под чат (заголовок/футер). Live использует остаток.",
        "live_stream.min_visible_lines": "Минимум строк Live даже на крошечных терминалах.",

        "response.title_format": "Формат заголовка response-панели. {num} = ' 5' / '' (для первой).",
        "response.title_format_compact_bullet": "Bullet перед заголовком в compact (не используется напрямую, для совместимости).",

        "subagent.max_width": "Максимальная ширина панелей субагентов = min(значение, ширина_терминала). 0 — на всю ширину терминала.",
        "subagent.max_concurrency": "Сколько субагентов внутри одной волны исполняются ОДНОВРЕМЕННО. Остальные ждут в очереди. Защита от rate-limit провайдера и пика по диску/FD при сотнях задач. 0/отрицательное = без лимита.",
        "subagent.block_threshold": "Если активных субагентов БОЛЬШЕ этого числа — все рисуются однострочно (компактно), иначе каждый занимает многострочный блок. Защита от разрыва терминала при десятках агентов. 0 = всегда блочный вид.",
        "subagent.block_separator": "Символ горизонтальной линии-разделителя между блоками субагентов (по умолчанию '─').",
        "subagent.prompt_lines": "Сколько строк задачи (prompt) показывать в блоке субагента (с переносом по словам).",
        "subagent.header_emoji": "Эмодзи в заголовке субагента ('🤖').",
        "subagent.done_emoji": "Эмодзи успешного завершения ('✓').",
        "subagent.error_emoji": "Эмодзи ошибки ('✗').",
    },

    "limits": {
        "max_width": 100,
        "compact_preview_lines": 8,
        "compact_preview_lines_shell": 5,
        "compact_head_lines": 10,
        "compact_tail_lines": 10,
        "panel_head_lines": 5,
        "panel_tail_lines": 5,
        "streaming_max_lines": 40,
        "max_result_length": 15000,
        "partial_lang_bypass_threshold": 50000,
        "think_snippet_max_len": 140,
    },

    "paddings": {
        "response_panel": [0, 2],
        "tool_panel": [0, 0],
        "code_block": [0, 2],
        "diff_table": [0, 1],
        "subagent_panel": [0, 1],
        "reasoning_panel": [1, 2],
        "think_panel": [0, 2],
        "executor_panel": [0, 1],
    },

    "symbols": {
        "separator_horizontal": "─",
        "tree_branch": "├─ ",
        "tree_last": "└─ ",
        "tree_branch_long": "├── ",
        "tree_last_long": "└── ",
        "tree_pipe": "│   ",
        "tree_blank": "    ",
        "summary_prefix": "⎿  ",
        "compact_separator_prefix": "└─",
        "vertical_pipe": "│",
        "diff_separator": "  │  ",
        "bullet_active": "● ",
        "bullet_dim": "○ ",
        "cursor": "▌",
        "ellipsis": "…",
        "thinking_emoji": "💭",
        "interrupt_marker": "■ ",
    },

    "spinner": {
        "frames": [
            "\u280B", "\u2819", "\u2839", "\u2838",
            "\u283C", "\u2834", "\u2826", "\u2827",
            "\u2807", "\u280F",
        ],
        "interval": 0.08,
        "exec_frames": [
            "\u25CF", "\u25CF", "\u25CF", "\u25CF",
            " ", " ", " ", " ",
        ],
    },

    "diff_colors": {
        "bg_delete": "#2a0808",
        "bg_add": "#082a08",
        "fg_delete": "#ff6b6b",
        "fg_add": "#6bff6b",
        "prefix_delete": "- ",
        "prefix_add": "+ ",
        "prefix_equal": "  ",
    },

    "tools": {
        "poll":          {"label": "Poll",       "emoji": "❓", "color_role": "accent"},
        "shell":         {"label": "Shell",      "emoji": "⏺",  "color_role": "warning"},
        "read_files":    {"label": "Read",       "emoji": "📖", "color_role": "info"},
        "read_file":     {"label": "Read",       "emoji": "📖", "color_role": "info"},
        "write_file":    {"label": "Write",      "emoji": "📝", "color_role": "success"},
        "patch_file":    {"label": "Patch",      "emoji": "🔧", "color_role": "warning"},
        "create_file":   {"label": "Create",     "emoji": "✨", "color_role": "success"},
        "delete_file":   {"label": "Delete",     "emoji": "🗑 ", "color_role": "error"},
        "rename_file":   {"label": "Rename",     "emoji": "📛", "color_role": "info"},
        "copy_file":     {"label": "Copy",       "emoji": "📋", "color_role": "info"},
        "move_file":     {"label": "Move",       "emoji": "📦", "color_role": "info"},
        "ls":            {"label": "List",       "emoji": "📂", "color_role": "info"},
        "tree":          {"label": "Tree",       "emoji": "🌳", "color_role": "info"},
        "mkdir":         {"label": "Mkdir",      "emoji": "📁", "color_role": "success"},
        "rmdir":         {"label": "Rmdir",      "emoji": "🗑 ", "color_role": "error"},
        "find_files":    {"label": "Find",       "emoji": "🔍", "color_role": "info"},
        "grep_files":    {"label": "Grep",       "emoji": "🔎", "color_role": "magenta"},
        "web_search":    {"label": "Search",     "emoji": "🌐", "color_role": "accent"},
        "image_search":  {"label": "Images",     "emoji": "🖼 ", "color_role": "accent"},
        "ssh":           {"label": "SSH",        "emoji": "🔗", "color_role": "magenta"},
        "subagent":      {"label": "Subagent",   "emoji": "🤖", "color_role": "magenta"},
        "plan":          {"label": "Plan",       "emoji": "📋", "color_role": "accent"},
        "skill":         {"label": "Skill",      "emoji": "🎓", "color_role": "info"},
        "create_docx":   {"label": "DOCX",       "emoji": "📄", "color_role": "success"},
        "docx_screenshot": {"label": "Docx Shot",  "emoji": "🖼 ", "color_role": "info"},
        "think":         {"label": "Think",      "emoji": "💭", "color_role": "purple"},
        "lsp_definition":{"label": "Definition", "emoji": "🎯", "color_role": "warning"},
        "lsp_references":{"label": "References", "emoji": "🔗", "color_role": "warning"},
        "lsp_hover":     {"label": "Hover",      "emoji": "💡", "color_role": "warning"},
        "lsp_diagnostics":{"label": "Diagnostics","emoji": "🩺", "color_role": "warning"},
        "apply_diff":    {"label": "Diff",       "emoji": "🔧", "color_role": "warning"},
        "expand_tool_result": {"label": "Expand","emoji": "🔍", "color_role": "info"},
        "workflow":      {"label": "Workflow",   "emoji": "🧩", "color_role": "magenta"},
        "memory_write":  {"label": "Memory",     "emoji": "🧠", "color_role": "purple"},
        "memory_list":   {"label": "Memory",     "emoji": "🧠", "color_role": "purple"},
        "memory_read":   {"label": "Memory",     "emoji": "🧠", "color_role": "purple"},
        "_default":      {"label": "Tool",       "emoji": "⏺",  "color_role": "warning"},
        "_mcp":          {"label": "{server}.{tool}", "emoji": "🔌", "color_role": "magenta"},
    },

    "indicators": {
        "thinking_suffix": "thinking…",
        "writing_prefix": "writing ",
        "writing_suffix": "…",
        "interrupt_text": "Waiting for response",
        "interrupt_hint": "(Ctrl+C = stop)",
    },

    "live_stream": {
        "refresh_per_second": 8,
        "reserved_lines": 14,
        "min_visible_lines": 10,
    },

    "response": {
        "title_format": "— Response{num}",
        "title_format_compact_bullet": "● ",
    },

    "subagent": {
        "max_width": 0,
        "header_emoji": "🤖",
        "done_emoji": "✓",
        "error_emoji": "✗",
        "max_concurrency": 12,
        "block_threshold": 5,
        "block_separator": "─",
        "prompt_lines": 2,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Глубоко мержит override в base. Не мутирует входы."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class UIConfig:
    """Singleton-обёртка с автогенерацией ui.json и dotted-path get'ом."""

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None

    def _ensure_loaded(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            _log.warning("ui_config: failed to create BASE_DIR: %s", e)

        if not UI_FILE.exists():
            self._write_defaults()
            self._data = dict(DEFAULTS)
            return self._data

        try:
            with UI_FILE.open("r", encoding="utf-8") as f:
                user_data = json.load(f)
            if not isinstance(user_data, dict):
                _log.warning("ui_config: %s is not a dict, using defaults", UI_FILE)
                self._data = dict(DEFAULTS)
            else:
                # Мержим, чтобы новые ключи из DEFAULTS подтянулись автоматически
                self._data = _deep_merge(DEFAULTS, user_data)
        except (json.JSONDecodeError, OSError) as e:
            _log.error("ui_config: failed to read %s: %s — using defaults", UI_FILE, e)
            self._data = dict(DEFAULTS)
        return self._data

    def _write_defaults(self) -> None:
        try:
            with UI_FILE.open("w", encoding="utf-8") as f:
                json.dump(DEFAULTS, f, ensure_ascii=False, indent=2)
        except OSError as e:
            _log.error("ui_config: failed to write defaults to %s: %s", UI_FILE, e)

    def reload(self) -> None:
        self._data = None
        self._ensure_loaded()

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted path: ui.get('tools.shell.emoji', '⏺')."""
        data = self._ensure_loaded()
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def tool(self, tool_name: str) -> dict[str, str]:
        """Возвращает {label, emoji, color_role} для инструмента."""
        tools = self._ensure_loaded().get("tools", {})
        if tool_name in tools:
            return tools[tool_name]
        return tools.get("_default", DEFAULTS["tools"]["_default"])

    def mcp_display(self, server: str, tool: str) -> dict[str, str]:
        """Возвращает display для MCP-инструмента (с подстановкой server/tool)."""
        tpl = self._ensure_loaded().get("tools", {}).get("_mcp", DEFAULTS["tools"]["_mcp"])
        label = tpl.get("label", "{server}.{tool}").format(server=server, tool=tool)
        return {
            "label": label,
            "emoji": tpl.get("emoji", "🔌"),
            "color_role": tpl.get("color_role", "magenta"),
        }


ui = UIConfig()