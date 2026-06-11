# ruff: noqa: E402

import os
import sys

# Корень пакетов — каталог этого файла (src/). Гарантируем, что он в sys.path,
# чтобы абсолютные импорты (from agent..., import tools, ...) работали и при
# запуске `python src/main.py` из корня репозитория, и при `python main.py`
# из самого src/.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import logger as _logger_mod

_logger = _logger_mod.logger

# Поднимаем soft-лимит файловых дескрипторов на Unix: httpx-стримы +
# открытые файлы сессий быстро упираются в дефолтные 1024 на Linux.
if sys.platform != "win32":
    try:
        import resource
        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        _target = min(_hard, 8192)
        if _soft < _target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (_target, _hard))
            _logger.info("RLIMIT_NOFILE raised: {} -> {}".format(_soft, _target))
    except Exception as _e:
        _logger.warning("failed to raise RLIMIT_NOFILE: {}".format(_e))

import click

from commands.interactive import interactive
from commands.headless import run_command

@click.group()
def cli():
    """necli-api — AI chat from the terminal (API-only mode, no browser)."""
    _logger.info("necli-api CLI start")

cli.add_command(interactive)
cli.add_command(run_command)

if __name__ == "__main__":
    cli()