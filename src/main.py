import atexit
import importlib
import sys
from typing import ClassVar

import click

import logger as _logger_mod
from ui.terminal_title import reset_terminal_title

_logger = _logger_mod.logger
_logger_mod.setup_logging()

if sys.platform != "win32":
    try:
        import resource

        soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        target_limit = min(hard_limit, 8192)
        if soft_limit < target_limit:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target_limit, hard_limit))
    except (OSError, ValueError) as exc:
        _logger.warning("failed to raise RLIMIT_NOFILE: {}", exc)

atexit.register(reset_terminal_title)


class LazyCommandGroup(click.Group):
    """Load command modules only when their subcommand is requested."""

    _lazy: ClassVar[dict[str, tuple[str, str]]] = {
        "cli": ("commands.interactive", "interactive"),
        "run": ("commands.headless", "run_command"),
    }

    def list_commands(self, ctx):
        return sorted(set(super().list_commands(ctx)) | set(self._lazy))

    def get_command(self, ctx, name):
        command = super().get_command(ctx, name)
        if command is not None:
            return command
        target = self._lazy.get(name)
        if target is None:
            return None
        module_name, attribute = target
        return getattr(importlib.import_module(module_name), attribute)

    def format_commands(self, ctx, formatter):
        rows = [("cli", "Interactive terminal chat"), ("run", "Headless one-shot/CI mode")]
        with formatter.section("Commands"):
            formatter.write_dl(rows)


@click.group(cls=LazyCommandGroup)
def cli():
    """necli-api — AI chat from the terminal (API-only mode, no browser)."""
    _logger.info("necli-api CLI start")


if __name__ == "__main__":
    cli()
