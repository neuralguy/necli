"""Снэпшоты ФС и детект внешних изменений между раундами агента."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from logger import logger
from config import is_ignored_dir

# Дополнительные паттерны имён, которые игнорируем
_IGNORE_SUFFIXES = {".pyc", ".pyo", ".swp", ".swo", ".log"}

# Hard-cap чтобы snapshot не зависал на гигантских репах
_MAX_FILES = 5000
_MAX_BLOCK_ENTRIES = 50

# Throttle: не делать новый снимок чаще чем раз в N секунд per-workdir.
_MIN_SNAPSHOT_INTERVAL_SEC = 2.0
_LAST_SNAPSHOT_AT: dict[str, float] = {}
_LAST_SNAPSHOT: dict[str, dict[str, tuple[float, int]]] = {}


def take_snapshot_throttled(working_dir: str) -> dict[str, tuple[float, int]]:
    """Версия take_snapshot с кэшированием.

    Если с прошлого снимка прошло меньше _MIN_SNAPSHOT_INTERVAL_SEC секунд —
    возвращается прошлый снимок. Иначе делается новый и кладётся в кэш.
    """
    import time as _t
    now = _t.monotonic()
    last_at = _LAST_SNAPSHOT_AT.get(working_dir, 0.0)
    if (now - last_at) < _MIN_SNAPSHOT_INTERVAL_SEC and working_dir in _LAST_SNAPSHOT:
        return _LAST_SNAPSHOT[working_dir]
    snap = take_snapshot(working_dir)
    _LAST_SNAPSHOT[working_dir] = snap
    _LAST_SNAPSHOT_AT[working_dir] = now
    return snap


def take_snapshot(working_dir: str) -> dict[str, tuple[float, int]]:
    """Возвращает {relpath: (mtime, size)} для всех файлов проекта.

    Игнорирует виртуальные/кэш-директории и временные файлы.
    """
    root = Path(working_dir)
    if not root.is_dir():
        return {}

    snap: dict[str, tuple[float, int]] = {}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_ignored_dir(d)]
        for fname in filenames:
            if any(fname.endswith(s) for s in _IGNORE_SUFFIXES):
                continue
            fpath = Path(dirpath) / fname
            try:
                st = fpath.stat()
            except (OSError, PermissionError):
                continue
            try:
                rel = str(fpath.relative_to(root))
            except ValueError:
                rel = str(fpath)
            snap[rel] = (st.st_mtime, st.st_size)
            count += 1
            if count >= _MAX_FILES:
                logger.warning(
                    "fs_watcher: snapshot cap hit ({} files), truncating",
                    _MAX_FILES,
                )
                return snap
    return snap


@dataclass
class ExternalChange:
    op: str         # "created" | "modified" | "deleted"
    path: str
    size: int = 0   # для created/modified — новый размер


def _normalize_own(p: str) -> str:
    """Нормализует путь к relpath: убирает ./ префикс и слэши."""
    if p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def diff_snapshots(
    old: dict[str, tuple[float, int]],
    new: dict[str, tuple[float, int]],
    own_paths: set[str] | None = None,
) -> list[ExternalChange]:
    """Возвращает изменения new vs old, исключая own_paths.

    Делает эвристическую склейку rename: если deleted+created имеют одинаковый
    size и нет других кандидатов на этот size, склеивается в один modified.
    """
    own = set()
    if own_paths:
        for p in own_paths:
            own.add(_normalize_own(p))

    created: list[ExternalChange] = []
    modified: list[ExternalChange] = []
    deleted: list[ExternalChange] = []

    for path, (mt, sz) in new.items():
        if path in own:
            continue
        prev = old.get(path)
        if prev is None:
            created.append(ExternalChange(op="created", path=path, size=sz))
        elif prev != (mt, sz):
            modified.append(ExternalChange(op="modified", path=path, size=sz))

    for path in old.keys() - new.keys():
        if path in own:
            continue
        old_size = old[path][1]
        deleted.append(ExternalChange(op="deleted", path=path, size=old_size))

    # Эвристика rename: одинаковый size, по одному кандидату с обеих сторон.
    by_size_del: dict[int, list[ExternalChange]] = {}
    for d in deleted:
        by_size_del.setdefault(d.size, []).append(d)
    by_size_cre: dict[int, list[ExternalChange]] = {}
    for c in created:
        by_size_cre.setdefault(c.size, []).append(c)

    renamed_pairs: list[tuple[ExternalChange, ExternalChange]] = []
    for size, dels in by_size_del.items():
        cres = by_size_cre.get(size, [])
        if len(dels) == 1 and len(cres) == 1 and size > 0:
            renamed_pairs.append((dels[0], cres[0]))

    rename_changes: list[ExternalChange] = []
    for d, c in renamed_pairs:
        deleted.remove(d)
        created.remove(c)
        rename_changes.append(
            ExternalChange(op="modified", path=f"{d.path} → {c.path}", size=c.size),
        )

    return deleted + rename_changes + modified + created


def format_changes_block(changes: list[ExternalChange]) -> str:
    """Форматирует блок для вставки в tool_results-сообщение."""
    if not changes:
        return ""

    icons = {"created": "✚", "modified": "✎", "deleted": "✗"}

    # Сортировка: deleted → modified → created, внутри по пути
    order = {"deleted": 0, "modified": 1, "created": 2}
    sorted_ch = sorted(changes, key=lambda c: (order.get(c.op, 99), c.path))

    lines = ["--- EXTERNAL FILE CHANGES (made outside the agent) ---"]
    extra = 0
    for c in sorted_ch:
        if len(lines) - 1 >= _MAX_BLOCK_ENTRIES:
            extra += 1
            continue
        icon = icons.get(c.op, "?")
        if c.op == "deleted":
            lines.append(f"{icon} {c.op:<8} {c.path}")
        else:
            lines.append(f"{icon} {c.op:<8} {c.path}  ({c.size}b)")
    if extra:
        lines.append(f"... and {extra} more")
    lines.append("--- END EXTERNAL FILE CHANGES ---")
    return "\n".join(lines)