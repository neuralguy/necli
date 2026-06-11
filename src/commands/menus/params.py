from rich.console import Console

import config
from logger import logger
from ui.menu import select_menu

console = Console()


def _invalidate_api_llm() -> None:
    """Сбрасывает закешированный LLM в активной ApiSession и общем реестре,
    чтобы новые temperature/max_tokens применились при следующем запросе.
    """
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


def _fmt_max_tokens(v: int) -> str:
    return f"{v}" if v > 0 else "default (provider)"


def params_interactive() -> None:
    while True:
        temp = float(config.get("temperature", 0.7) or 0.7)
        max_tok = int(config.get("max_tokens", 0) or 0)

        console.print()
        console.print("  [bold]Generation parameters[/bold]")
        console.print(f"  [dim]temperature:[/dim] [yellow]{temp:.2f}[/yellow]")
        console.print(f"  [dim]max_tokens:[/dim]  [yellow]{_fmt_max_tokens(max_tok)}[/yellow]")
        console.print()

        items = [
            {"label": f"temperature  ({temp:.2f})", "hint": "0.0 — deterministic, 1.0+ — creative"},
            {"label": f"max_tokens   ({_fmt_max_tokens(max_tok)})", "hint": "0 = provider default; e.g. 16384"},
            {"label": "← Back"},
        ]
        choice = select_menu(items, title="Parameters")
        if choice is None or choice == 2:
            return

        if choice == 0:
            try:
                console.print()
                raw = console.input(f"  [bold]New temperature[/bold] [dim]({temp:.2f}):[/dim] ").strip()
                if not raw:
                    continue
                val = float(raw)
                if val < 0 or val > 2:
                    console.print("  [red]Out of range. Use 0.0 — 2.0[/red]")
                    continue
                config.set_value("temperature", val)
                _invalidate_api_llm()
                console.print(f"  [green]✓[/green] temperature = [yellow]{val:.2f}[/yellow]")
            except ValueError:
                console.print("  [red]Invalid number[/red]")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if choice == 1:
            try:
                console.print()
                raw = console.input(
                    f"  [bold]New max_tokens[/bold] [dim]({_fmt_max_tokens(max_tok)}, 0 = provider default):[/dim] "
                ).strip()
                if not raw:
                    continue
                val = int(raw)
                if val < 0 or val > 200000:
                    console.print("  [red]Out of range. Use 0 — 200000[/red]")
                    continue
                config.set_value("max_tokens", val)
                _invalidate_api_llm()
                console.print(f"  [green]✓[/green] max_tokens = [yellow]{_fmt_max_tokens(val)}[/yellow]")
            except ValueError:
                console.print("  [red]Invalid integer[/red]")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue