import os
import subprocess

from rich.console import Console

from agent.agent_presets import (
    create_preset,
    get_agents_dir,
    list_presets,
    load_preset,
    remove_preset,
)
from config.i18n import t as _
from ui.menu import select_menu

console = Console()


def agents_interactive():
    while True:
        presets = list_presets()
        if not presets:
            console.print(
                f"  [dim]No agent presets yet ({get_agents_dir()})[/dim]"
            )
            items = [{"label": "Create preset…"}]
            choice = select_menu(items)
            if choice == 0:
                _preset_create_interactive()
                continue
            return

        items = []
        for p in presets:
            desc = p.description[:50] if p.description else "—"
            meta = []
            if p.model:
                meta.append(p.model)
            if p.mode != "agent":
                meta.append(p.mode)
            if p.tools:
                meta.append(f"{len(p.tools)} tools")
            hint = desc + (f"  [{', '.join(meta)}]" if meta else "")
            items.append({"label": p.name, "hint": hint})
        items.append({"label": "Create preset…", "hint": ""})

        choice = select_menu(items, title="Agent presets")
        if choice is None:
            return
        if choice == len(presets):
            _preset_create_interactive()
            continue

        action = _preset_detail_menu(presets[choice])
        if action == "back":
            continue
        return


def _preset_detail_menu(preset):
    while True:
        desc = preset.description or "—"
        meta = []
        if preset.model:
            meta.append(f"model={preset.model}")
        meta.append(f"mode={preset.mode}")
        if preset.tools:
            meta.append(f"tools={', '.join(preset.tools)}")

        body_preview = preset.body[:300]
        if len(preset.body) > 300:
            body_preview += "..."

        console.print()
        console.print(f"  [bold yellow]{preset.name}[/bold yellow]")
        console.print(f"  [dim]{desc}[/dim]")
        console.print(f"  [dim]{'  '.join(meta)}[/dim]")
        console.print(f"  [dim]{preset.path / 'AGENT.md'}[/dim]")
        console.print()
        for line in body_preview.splitlines():
            console.print(f"  {line}")
        console.print()

        actions = [
            {"label": "Edit"},
            {"label": _("api.delete"), "hint": _("api.delete_permanent")},
            {"label": _("common.back")},
        ]
        choice = select_menu(actions)

        if choice is None or choice == 2:
            return "back"

        if choice == 0:
            editor = os.environ.get("EDITOR", "nano")
            preset_file = str(preset.path / "AGENT.md")
            try:
                subprocess.run([editor, preset_file])
                reloaded = load_preset(preset.name)
                if reloaded is None:
                    return "back"
                preset = reloaded
            except Exception as e:
                console.print(f"  [red]Edit error: {e}[/red]")
            continue

        if choice == 1:
            confirm = [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}]
            c = select_menu(confirm, title=f"Delete preset '{preset.name}'?")
            if c == 0:
                remove_preset(preset.name)
                return "back"
            continue


def _preset_create_interactive():
    console.print()
    try:
        name = console.input("  [bold]Name:[/bold] ").strip()
        if not name:
            return
        desc = console.input("  [bold]Description:[/bold] ").strip()
        model = console.input("  [bold]Model (optional):[/bold] ").strip() or None
        body = f"Your ROLE is {name}.\n\nDescribe the role instructions here."
        preset = create_preset(name, desc or name, body, model=model)
        console.print(f"  [green]✓[/green] Created preset '{preset.name}'")
        editor = os.environ.get("EDITOR", "nano")
        preset_file = str(preset.path / "AGENT.md")
        console.print(f"  [dim]Opening {editor}…[/dim]")
        subprocess.run([editor, preset_file])
        console.print(f"  [dim]{preset_file}[/dim]")
    except (KeyboardInterrupt, EOFError):
        console.print()
