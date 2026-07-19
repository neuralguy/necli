"""Общая логика фильтрации файлов/директорий для completer и file_context."""

import config

# Суффиксы, которые всегда игнорируются (объединение из completer + file_context)
_IGNORE_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll",
    ".exe", ".o", ".obj", ".class", ".jar",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
})


def should_ignore(name: str, is_dir: bool) -> bool:
    """Проверяет, нужно ли игнорировать файл/директорию."""
    if name.startswith("."):
        return True
    if is_dir and name in config.IGNORE_DIRS:
        return True
    if not is_dir:
        for suffix in _IGNORE_SUFFIXES:
            if name.endswith(suffix):
                return True
    return False
