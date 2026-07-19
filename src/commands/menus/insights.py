"""Menu /insights — анализ всего общения → HTML-отчёт + факты в память."""

import asyncio

from rich.console import Console
from rich.markup import escape

from config.i18n import t as _
from logger import logger
from tools._paths import get_working_dir

console = Console()


def _run_async(coro):
    """Запускает корутину из синхронного slash-обработчика.

    /insights вызывается из уже работающего event loop интерактивного цикла,
    поэтому asyncio.run() здесь падает. Выполняем корутину в отдельном потоке
    с собственным циклом — работает и при наличии активного loop, и без него.
    """
    result: dict = {}

    def _worker():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(coro)
        except BaseException as e:
            result["error"] = e
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    import threading
    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join()
    if "error" in result:
        raise result["error"]
    return result["value"]

def insights_interactive() -> None:
    from memory.insights import generate_insights

    console.print(f"  [dim]{_('insights.working')}[/dim]")
    try:
        with console.status(_("insights.working"), spinner="dots"):
            result = _run_async(
                generate_insights(get_working_dir(), persist_memory=False)
            )
    except RuntimeError as e:
        if "no sessions" in str(e):
            console.print(f"  [yellow]{_('insights.no_sessions')}[/yellow]")
            return
        logger.error("insights failed: {}", e)
        console.print(f"  [red]{_('insights.failed', err=escape(str(e)))}[/red]")
        return
    except Exception as e:
        logger.opt(exception=True).error("insights failed: {}", e)
        console.print(f"  [red]{_('insights.failed', err=escape(str(e)))}[/red]")
        return

    path = result["report_path"]
    console.print(f"  [green]✓[/green] {_('insights.done', path=escape(str(path)))}")
