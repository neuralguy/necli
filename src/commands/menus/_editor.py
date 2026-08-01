"""Запуск $EDITOR поверх постоянного Application.

Отдельный модуль, потому что этим пользуются и /skills, и /agents, а ошибиться
здесь дорого: Application держит tty в raw-режиме и сам владеет отрисовкой,
поэтому наивный subprocess.run() отдал бы nano/vim терминал без эха, без
корректного размера и с чужим курсором.
"""

from __future__ import annotations

import os
import subprocess

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app_or_none


def editor_command() -> str:
    """Имя редактора — нужно ещё и для сообщения «открываю …»."""
    return os.environ.get("EDITOR", "nano")


async def open_in_editor(path: str) -> None:
    """Открыть файл во внешнем редакторе, вернув ему нормальный терминал.

    `run_in_terminal` гасит рендер Application, отцепляет его от stdin и
    переводит tty обратно в cooked-режим на время вызова, а `in_executor=True`
    уводит блокирующий subprocess.run в поток — иначе на всё время правки
    встал бы event loop (стрим агента, Telegram-бридж, спиннеры).
    """
    editor = editor_command()

    def _spawn() -> None:
        subprocess.run([editor, path])

    app = get_app_or_none()
    if app is None or not app.is_running:
        # Headless / до старта Application — терминалом никто не владеет.
        _spawn()
        return
    await run_in_terminal(_spawn, in_executor=True)


__all__ = ["editor_command", "open_in_editor"]
