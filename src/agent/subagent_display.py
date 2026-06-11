"""Визуализация запуска/статуса/завершения субагентов."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config.themes import t
from config.ui import ui

console = Console()


def _w() -> int:
    return min(int(ui.get("subagent.max_width", 100)), console.width)


def show_subagent_start(
    index: int, total: int, mode: str, prompt: str,
    model_label: str = "",
):
    """Показывает панель запуска субагента."""
    mode_icon = ui.get("subagent.header_emoji", "\U0001f916")
    mode_label = "agent"
    short_prompt = prompt[:120] + ("..." if len(prompt) > 120 else "")
    text = Text()
    text.append(f"  {mode_icon} ", style=f"bold {t('magenta')}")
    text.append(f"Subagent {index + 1}/{total}", style=f"bold {t('magenta')}")
    text.append(f" [{mode_label}]", style=t("purple"))
    if model_label:
        text.append(f" · {model_label}", style="dim")
    text.append(f"\n  {short_prompt}", style="dim")
    pad = tuple(ui.get("paddings.subagent_panel", [0, 1]))
    console.print(
        Panel(text, border_style=t("magenta"), padding=pad, width=_w()),
    )


def show_subagent_status(index: int, message: str):
    """Показывает обновление статуса субагента."""
    icon = ui.get("subagent.header_emoji", "\U0001f916")
    console.print(
        f"  [dim {t('magenta')}]{icon} Subagent {index + 1}: {message}"
        f"[/dim {t('magenta')}]",
    )


def show_subagent_done(index: int, result=None):
    """Показывает результат завершения субагента."""
    if result is None:
        return
    pad = tuple(ui.get("paddings.subagent_panel", [0, 1]))
    err_emoji = ui.get("subagent.error_emoji", "\u2717")
    done_emoji = ui.get("subagent.done_emoji", "\u2713")
    if result.error:
        text = Text()
        text.append(f"  {err_emoji} Subagent {index + 1} ", style="bold red")
        text.append(f"FAILED: {result.error[:200]}", style="red")
        console.print(Panel(text, border_style="red", padding=pad, width=_w()))
    else:
        elapsed = f"{result.elapsed:.1f}s" if result.elapsed else ""
        iters = f"{result.iterations} iter" if result.iterations else ""
        stats = ", ".join(filter(None, [iters, elapsed]))
        text = Text()
        text.append(f"  {done_emoji} Subagent {index + 1} ", style=f"bold {t('success')}")
        text.append(f"[{result.mode}]", style=t("purple"))
        model_label = getattr(result, "model_label", "") or ""
        if model_label:
            text.append(f" · {model_label}", style="dim")
        if stats:
            text.append(f" ({stats})", style="dim")
        response_preview = result.response[:200] + ("..." if len(result.response) > 200 else "")
        text.append(f"\n  {response_preview}", style="")
        console.print(
            Panel(text, border_style=t("success"), padding=pad, width=_w()),
        )