"""Общие утилиты для работы с путями и рабочей директорией.

Используются в file_ops, dir_ops, shell.
Рабочая директория хранится в ContextVar — это даёт автоматическую
изоляцию между параллельными asyncio-тасками (каждый таск получает
свою копию контекста при создании). Это критично для субагентов,
работающих в git worktree'ах параллельно с главным агентом.
"""

import contextvars
import os
from pathlib import Path


_working_dir_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "necli_working_dir",
    default=os.getcwd(),
)


class WorkingDirectory:
    """Тонкая обёртка над ContextVar — для обратной совместимости.

    Опциональный path в конструкторе сразу устанавливает рабочую директорию
    (без аргумента — текущее значение ContextVar / cwd).
    """

    def __init__(self, path: str | None = None):
        if path is not None:
            _working_dir_var.set(path)

    def get(self) -> str:
        return _working_dir_var.get()

    def set(self, path: str):
        _working_dir_var.set(path)


def set_working_dir(path: str):
    _working_dir_var.set(path)


def get_working_dir() -> str:
    return _working_dir_var.get()


def use_working_dir(path: str):
    """Контекст-менеджер для временной подмены working dir.

    Использует ContextVar.set/reset — изменение видно только в текущем
    asyncio-таске и его дочерних (Task копирует context при создании).

        with use_working_dir("/tmp/sub-1"):
            ... # tools видят /tmp/sub-1
    """
    class _Ctx:
        def __init__(self, p):
            self._p = p
            self._tok = None

        def __enter__(self):
            self._tok = _working_dir_var.set(self._p)
            return self

        def __exit__(self, *exc):
            if self._tok is not None:
                _working_dir_var.reset(self._tok)

    return _Ctx(path)


def resolve_path(path: str) -> Path:
    """Резолвит путь относительно рабочей директории.

    Раскрывает ~, переменные окружения, относительные пути.
    Нормализует через normpath (убирает .., .), но НЕ через realpath —
    realpath следует за симлинками, что ломает изоляцию субагентов в
    git worktree (если в worktree есть симлинк на main, запись утечёт).
    """
    p = os.path.expanduser(path)
    p = os.path.expandvars(p)
    if not os.path.isabs(p):
        p = os.path.join(get_working_dir(), p)
    return Path(os.path.normpath(p))


def clean_path(val) -> str:
    """Нормализует путь из аргументов: strip + убирает обрамляющие кавычки.

    Если на вход прилетает list/tuple — берёт первый элемент (модели иногда
    кладут одиночный путь в массив).
    """
    if isinstance(val, (list, tuple)):
        val = val[0] if val else ""
    if not isinstance(val, str):
        val = str(val)
    val = val.strip()
    if len(val) >= 2:
        if (val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'"):
            val = val[1:-1]
    return val