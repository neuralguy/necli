"""Menu /insights — анализ всего общения → HTML-отчёт + факты в память."""

import asyncio

from logger import logger
from tools._paths import get_working_dir


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

    try:
        _run_async(
            generate_insights(get_working_dir(), persist_memory=False)
        )
    except RuntimeError as e:
        if "no sessions" in str(e):
            return
        logger.error("insights failed: {}", e)
        return
    except Exception as e:
        logger.opt(exception=True).error("insights failed: {}", e)
        return
