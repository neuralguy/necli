"""Workflow runs browser."""

from __future__ import annotations

import json
import os
from datetime import datetime

from rich.console import Console
from rich.table import Table

from config.i18n import t as _
from config.themes import t
from tools._paths import get_working_dir

console = Console()


def workflows_interactive(rest: str = "") -> None:
    arg = (rest or "").strip()
    base = _runs_dir()
    if arg:
        _show_run(base, arg)
    else:
        _list_runs(base)


def _runs_dir() -> str:
    return os.path.join(get_working_dir(), ".data", "workflow_runs")


def _load_state(path: str) -> dict | None:
    try:
        with open(os.path.join(path, "state.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        console.print(f"  [red]{_('common.error')}: {e}[/red]")
        return None


def _iter_runs(base: str) -> list[dict]:
    if not os.path.isdir(base):
        return []
    runs = []
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if not os.path.isdir(path):
            continue
        state = _load_state(path)
        if not state:
            continue
        state["_path"] = path
        runs.append(state)
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs


def _list_runs(base: str) -> None:
    runs = _iter_runs(base)
    if not runs:
        console.print(f"  [dim]{_('workflows.no_runs')}[/dim]")
        return

    table = Table(
        title=_("workflows.title"),
        border_style="dim",
        padding=(0, 1),
        show_header=True,
        header_style="bold dim",
    )
    table.add_column("Run", style=t("accent"))
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Phases", justify="right")
    table.add_column("Agents", justify="right")
    table.add_column("Started", style="dim")

    for run in runs[:30]:
        phases = run.get("phases") or []
        agents = sum(len(p.get("agents") or []) for p in phases if isinstance(p, dict))
        table.add_row(
            str(run.get("id") or "")[:22],
            str(run.get("name") or ""),
            str(run.get("status") or ""),
            str(len(phases)),
            str(agents),
            _short_time(str(run.get("started_at") or "")),
        )
    console.print(table)
    console.print(f"  [dim]{_('workflows.show_hint')}[/dim]")


def _show_run(base: str, prefix: str) -> None:
    matches = [r for r in _iter_runs(base) if str(r.get("id") or "").startswith(prefix)]
    if not matches:
        console.print(f"  [red]{_('workflows.not_found', id=prefix)}[/red]")
        return
    if len(matches) > 1:
        console.print(f"  [yellow]{_('workflows.ambiguous', n=len(matches))}[/yellow]")
        for run in matches[:10]:
            console.print(f"  [dim]{run.get('id')}[/dim]")
        return

    run = matches[0]
    console.print(f"[bold {t('accent')}]{run.get('name')}[/bold {t('accent')}] [dim]{run.get('id')}[/dim]")
    console.print(f"  status: [bold]{run.get('status')}[/bold]")
    console.print(f"  state: [dim]{os.path.join(run.get('_path') or '', 'state.json')}[/dim]")
    if run.get("error"):
        console.print(f"  [red]{run.get('error')}[/red]")

    table = Table(border_style="dim", padding=(0, 1), show_header=True, header_style="bold dim")
    table.add_column("Phase", style=t("accent"))
    table.add_column("Status")
    table.add_column("Agents", justify="right")
    table.add_column("Cached", justify="right")
    table.add_column("Failed", justify="right")

    for phase in run.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        agents = [a for a in phase.get("agents") or [] if isinstance(a, dict)]
        cached = sum(1 for a in agents if a.get("cached"))
        failed = sum(1 for a in agents if a.get("status") == "failed")
        table.add_row(
            str(phase.get("title") or ""),
            str(phase.get("status") or ""),
            str(len(agents)),
            str(cached),
            str(failed),
        )
    console.print(table)


def _short_time(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M")