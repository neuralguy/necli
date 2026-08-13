from __future__ import annotations

import os

from .settings import get

TARGET_MODEL: str = os.getenv("NECLI_MODEL", get("model", "Claude Opus 4.6"))


class Limits:
    """Единый источник числовых лимитов (вместо магических чисел в модулях)."""

    MAX_FILE_SIZE_INLINE = 100 * 1024
    MAX_DIR_FILES = 30
    MAX_TOTAL_CONTEXT_SIZE = 300 * 1024
    TABLE_HEAD_ROWS = 100
    TABLE_TAIL_ROWS = 50
    TABLE_TRUNCATE_THRESHOLD = 200
    SHELL_TIMEOUT = 60
    BG_SHELL_TIMEOUT = 3600
    MAX_SUBAGENT_ITERATIONS = 100
    MAX_SUBAGENT_CONTEXT_TOKENS = 1_000_000
    MODEL_CALL_TIMEOUT = 240.0
    READ_CACHE_MAX_SESSIONS = 50
    STATIC_QUEUE_MAX = 500
    HISTORY_MAX_AGE_DAYS = 7
    ROW_WINDOW_SIZE = 4


# Канонический набор игнорируемых директорий для всех обходов ФС:
# tree, grep_files, fs_watcher snapshot, project_stats.
IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        "bower_components",
        "vendor",
        ".venv",
        "venv",
        "env",
        ".env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".tox",
        ".nox",
        ".cache",
        ".idea",
        ".vscode",
        ".eggs",
        ".data",
        "logs",
    }
)


def is_ignored_dir(name: str) -> bool:
    """True если директорию с таким именем нужно игнорировать.

    Покрывает явные имена из IGNORE_DIRS, а также шаблон *.egg-info.
    """
    if name in IGNORE_DIRS:
        return True
    return bool(name.endswith(".egg-info"))


# Канонический набор read-only инструментов (доступен в plan-mode,
# безопасно запускать параллельно). Используется в:
#   - tools/registry.py (PLANNING_TOOLS / READ_ONLY_TOOLS)
#   - apis/tool_schemas.py (_PLANNING_TOOL_NAMES — фильтр схем)
# Алиас "read" обрабатывается отдельно в is_tool_allowed.
# LSP-инструменты семантически read-only (навигация/диагностика, ничего не
# пишут) — поэтому доступны и в plan-режиме главного агента, и plan-субагентам.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "grep",
        "lsp_references",
        "lsp_diagnostics",
        "memory",
    }
)
