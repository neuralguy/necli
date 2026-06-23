from rich.console import Console

import config
from config import t as _
from logger import logger
from ui.menu import select_menu

console = Console()

# Допустимые схемы прокси (см. settings.py["proxy"]).
_VALID_SCHEMES = ("http://", "https://", "socks5://", "socks5h://", "socks4://")


def _invalidate_api_llm() -> None:
    """Сбрасывает закешированный LLM, чтобы новый proxy применился сразу."""
    try:
        from apis.agent_adapter import get_api_session
        sess = get_api_session()
        if sess is not None:
            sess._llm = None
            sess._llm_kwargs = {}
    except Exception:
        logger.debug("invalidate api session llm failed", exc_info=True)
    try:
        import apis.registry as _reg
        _reg._instances.clear()
    except Exception:
        logger.debug("clear api registry instances failed", exc_info=True)


def _validate(url: str) -> bool:
    return url.lower().startswith(_VALID_SCHEMES)


def proxy_interactive() -> None:
    while True:
        current = str(config.get("proxy", "") or "")
        shown = current if current else _("proxy.none")

        console.print()
        console.print(f"  [bold]{_('proxy.header')}[/bold]")
        console.print(f"  [dim]{_('proxy.current')}:[/dim] [yellow]{shown}[/yellow]")
        console.print(f"  [dim]{_('proxy.schemes_hint')}[/dim]")
        console.print()

        items = [
            {"label": _("proxy.set"), "hint": _("proxy.set_hint")},
            {"label": _("proxy.clear"), "hint": _("proxy.clear_hint")},
            {"label": "← Back"},
        ]
        choice = select_menu(items, title=_("proxy.title"))
        if choice is None or choice == 2:
            return

        if choice == 0:
            try:
                console.print()
                raw = console.input(
                    f"  [bold]{_('proxy.enter')}[/bold] [dim](http://… / socks5://…):[/dim] "
                ).strip()
                if not raw:
                    continue
                if not _validate(raw):
                    console.print(f"  [red]{_('proxy.invalid')}[/red]")
                    continue
                config.set_value("proxy", raw)
                _invalidate_api_llm()
                console.print(f"  [green]✓[/green] proxy = [yellow]{raw}[/yellow]")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if choice == 1:
            config.set_value("proxy", "")
            _invalidate_api_llm()
            console.print(f"  [green]✓[/green] {_('proxy.cleared')}")
            continue
