from rich.console import Console

import config
from config import t as _
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
    return f"{v}" if v > 0 else _("params.default_provider")


def _fmt_reasoning_effort(v: str) -> str:
    if v == "low":
        return _("params.effort_low")
    elif v == "medium":
        return _("params.effort_medium")
    elif v == "high":
        return _("params.effort_high")
    return _("params.default_provider")


def params_interactive() -> None:
    while True:
        temp = float(config.get("temperature", 0.7) or 0.7)
        max_tok = int(config.get("max_tokens", 0) or 0)
        effort = str(config.get("reasoning_effort", "") or "")

        console.print()
        console.print(f"  [bold]{_('params.header')}[/bold]")
        console.print(f"  [dim]{_('params.temperature')}:[/dim]      [yellow]{temp:.2f}[/yellow]")
        console.print(f"  [dim]{_('params.max_tokens')}:[/dim]       [yellow]{_fmt_max_tokens(max_tok)}[/yellow]")
        console.print(f"  [dim]{_('params.reasoning_effort')}:[/dim] [yellow]{_fmt_reasoning_effort(effort)}[/yellow]")
        console.print()

        items = [
            {"label": f"{_('params.temperature')}       ({temp:.2f})", "hint": _("params.temp_hint")},
            {"label": f"{_('params.max_tokens')}        ({_fmt_max_tokens(max_tok)})", "hint": _("params.max_tokens_hint")},
            {"label": f"{_('params.reasoning_effort')}  ({_fmt_reasoning_effort(effort)})", "hint": _("params.reasoning_effort_hint")},
            {"label": "← Back"},
        ]
        choice = select_menu(items, title=_("params.title"))
        if choice is None or choice == 3:
            return

        if choice == 0:
            try:
                console.print()
                raw = console.input(f"  [bold]{_('params.new_temp')}[/bold] [dim]({temp:.2f}):[/dim] ").strip()
                if not raw:
                    continue
                val = float(raw)
                if val < 0 or val > 2:
                    console.print(f"  [red]{_('params.out_of_range_temp')}[/red]")
                    continue
                config.set_value("temperature", val)
                _invalidate_api_llm()
                console.print(f"  [green]✓[/green] temperature = [yellow]{val:.2f}[/yellow]")
            except ValueError:
                console.print(f"  [red]{_('params.invalid_number')}[/red]")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if choice == 1:
            try:
                console.print()
                raw = console.input(
                    f"  [bold]{_('params.new_max_tokens')}[/bold] [dim]({_fmt_max_tokens(max_tok)}, 0 = {_('params.default_provider')}):[/dim] "
                ).strip()
                if not raw:
                    continue
                val = int(raw)
                if val < 0 or val > 200000:
                    console.print(f"  [red]{_('params.out_of_range_max')}[/red]")
                    continue
                config.set_value("max_tokens", val)
                _invalidate_api_llm()
                console.print(f"  [green]✓[/green] max_tokens = [yellow]{_fmt_max_tokens(val)}[/yellow]")
            except ValueError:
                console.print(f"  [red]{_('params.invalid_int')}[/red]")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if choice == 2:
            items_effort = [
                {"label": _("params.effort_default"), "hint": _("params.effort_default_hint")},
                {"label": _("params.effort_low"), "hint": _("params.effort_low_hint")},
                {"label": _("params.effort_medium"), "hint": _("params.effort_medium_hint")},
                {"label": _("params.effort_high"), "hint": _("params.effort_high_hint")},
                {"label": "← Back"},
            ]
            sub_choice = select_menu(items_effort, title=_("params.reasoning_effort_title"))
            if sub_choice is None or sub_choice == 4:
                continue

            vals = ["", "low", "medium", "high"]
            new_val = vals[sub_choice]
            config.set_value("reasoning_effort", new_val)
            _invalidate_api_llm()
            console.print(f"  [green]✓[/green] reasoning_effort = [yellow]{_fmt_reasoning_effort(new_val)}[/yellow]")
            continue