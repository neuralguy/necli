"""Поиск стартовой строки find_text в файле для нумерации diff-превью.

Единственный публичный хелпер — _locate_find_in_file, используется в
agent/display.py:_compact_patch_preview для абсолютных номеров строк.
Сам side-by-side рендер живёт в agent/display.py.
"""

from __future__ import annotations

import os

# Кэш содержимого файлов для _locate_find_in_file: {abs_path: (mtime, size, content)}
_LOCATE_CACHE: dict[str, tuple[float, int, str]] = {}
_LOCATE_CACHE_MAX = 32


def _read_file_cached(abs_path: str) -> str | None:
    """Читает файл с mtime+size кэшем. None при ошибке."""
    try:
        st = os.stat(abs_path)
        cached = _LOCATE_CACHE.get(abs_path)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(_LOCATE_CACHE) >= _LOCATE_CACHE_MAX:
            _LOCATE_CACHE.pop(next(iter(_LOCATE_CACHE)))
        _LOCATE_CACHE[abs_path] = (st.st_mtime, st.st_size, content)
        return content
    except Exception:
        return None


def _locate_find_in_file(file_path: str, find_text: str) -> int:
    """Возвращает 1-based номер первой строки, где начинается find_text в файле.

    1 если не нашли (или файла нет) — чтобы рендер всё равно работал.
    """
    if not file_path or not find_text:
        return 1
    try:
        from tools._paths import resolve_path
        p = resolve_path(file_path)
        if not p.exists():
            return 1
        content = _read_file_cached(str(p))
        if content is None:
            return 1
    except Exception:
        return 1
    idx = content.find(find_text)
    if idx < 0:
        first_line = next((ln for ln in find_text.split("\n") if ln.strip()), "")
        if first_line:
            idx = content.find(first_line)
        if idx < 0:
            return 1
    return content.count("\n", 0, idx) + 1
