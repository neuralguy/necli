from __future__ import annotations

import os

from .settings import get

RESPONSE_TIMEOUT = int(os.getenv("NECLI_TIMEOUT", str(get("response_timeout", 180))))

TARGET_MODEL: str = os.getenv("NECLI_MODEL", get("model", "Claude Opus 4.6"))

# Максимум суб-агентов в ОДНОЙ фазе воркфлоу. Фаза накапливает агентов по всем
# своим parallel()/pipeline()-вызовам; при превышении — ошибка (а не тихий
# срез), чтобы автор скрипта дробил фазу явно.
MAX_WORKFLOW_AGENTS_PER_PHASE = 25

# Канонический набор игнорируемых директорий для всех обходов ФС:
# tree, grep_files, find_files, fs_watcher snapshot, project_stats.
IGNORE_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", "node_modules", "bower_components", "vendor",
    ".venv", "venv", "env", ".env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "target", ".next", ".nuxt",
    ".tox", ".nox", ".cache",
    ".idea", ".vscode",
    ".eggs",
    ".data", "logs",
})


def is_ignored_dir(name: str) -> bool:
    """True если директорию с таким именем нужно игнорировать.

    Покрывает явные имена из IGNORE_DIRS, а также шаблон *.egg-info.
    """
    if name in IGNORE_DIRS:
        return True
    if name.endswith(".egg-info"):
        return True
    return False


# Канонический набор read-only инструментов (доступен в plan-mode,
# безопасно запускать параллельно). Используется в:
#   - tools/registry.py (PLANNING_TOOLS / READ_ONLY_TOOLS)
#   - apis/tool_schemas.py (_PLANNING_TOOL_NAMES — фильтр схем)
# Алиас "read_file" обрабатывается отдельно в is_tool_allowed.
# LSP-инструменты семантически read-only (навигация/диагностика, ничего не
# пишут) — поэтому доступны и в plan-режиме главного агента, и plan-субагентам.
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_files", "grep_files", "tree", "ls", "find_files",
    "lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics",
    "memory_list", "memory_read",
})