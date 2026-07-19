import os
import subprocess
import sys

from rich.console import Console

from config.i18n import t as _
from skills import (
    activate_skill,
    create_skill,
    deactivate_skill,
    get_skills_dir,
    is_skill_active,
    list_skills,
    load_skill,
    remove_skill,
)
from ui.menu import select_menu

console = Console()


def skills_interactive():
    while True:
        all_skills = list_skills()
        if not all_skills:
            console.print(f"  [dim]{_('skills.no_skills', dir=get_skills_dir())}[/dim]")
            items = [{"label": _("skills.create")}]
            choice = select_menu(items)
            if choice == 0:
                _skill_create_interactive()
            return

        items = []
        for s in all_skills:
            active = "● " if is_skill_active(s.name) else "  "
            desc = s.description[:60] if s.description else "—"
            items.append({"label": f"{active}{s.name}", "hint": desc})
        items.append({"label": _("skills.create"), "hint": ""})

        choice = select_menu(items, title=_("skills.title"))
        if choice is None:
            return
        if choice == len(all_skills):
            _skill_create_interactive()
            continue

        skill = all_skills[choice]
        action = _skill_detail_menu(skill)
        if action == "back":
            continue
        return


def _skill_detail_menu(skill):
    while True:
        desc = skill.description or _("skills.no_description")
        active = is_skill_active(skill.name)
        status = f"[green]{_('skills.active')}[/green]" if active else f"[dim]{_('skills.inactive')}[/dim]"

        body_preview = skill.body[:300]
        if len(skill.body) > 300:
            body_preview += "..."
        body_lines = body_preview.splitlines()

        sys.stdout.write("\x1b7")
        sys.stdout.flush()

        console.print()
        console.print(f"  [bold yellow]{skill.name}[/bold yellow]  {status}")
        console.print(f"  [dim]{desc}[/dim]")
        console.print(f"  [dim]{skill.path / 'SKILL.md'}[/dim]")
        console.print()
        for line in body_lines:
            console.print(f"  {line}")
        console.print()

        toggle = _("skills.toggle_disable") if active else _("skills.toggle_enable")
        actions = [
            {"label": toggle, "hint": _("skills.toggle_hint_on") if not active else _("skills.toggle_hint_off")},
            {"label": _("skills.edit")},
            {"label": _("api.delete"), "hint": _("api.delete_permanent")},
            {"label": _("common.back")},
        ]
        choice = select_menu(actions)

        sys.stdout.write("\x1b8")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

        if choice is None or choice == 3:
            return "back"

        if choice == 0:
            if active:
                deactivate_skill(skill.name)
            else:
                activate_skill(skill.name)
            return "back"

        if choice == 1:
            editor = os.environ.get("EDITOR", "nano")
            skill_file = str(skill.path / "SKILL.md")
            try:
                subprocess.run([editor, skill_file])
                skill = load_skill(skill.name)
                if skill is None:
                    return "back"
            except Exception as e:
                console.print(f"  [red]{_('skills.edit_error', error=e)}[/red]")
            continue

        if choice == 2:
            confirm = [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}]
            c = select_menu(confirm, title=_("skills.delete_q", name=skill.name))
            if c == 0:
                remove_skill(skill.name)
                return "back"
            continue


def _skill_create_interactive():
    console.print()
    try:
        name = console.input(f"  [bold]{_('skills.field_name')}:[/bold] ").strip()
        if not name:
            return
        desc = console.input(f"  [bold]{_('skills.field_description')}:[/bold] ").strip()
        skill = create_skill(name, desc or name, f"# {name}\n\nSkill instructions here.")
        console.print(f"  [green]✓[/green] {_('skills.created', name=skill.name)}")
        editor = os.environ.get("EDITOR", "nano")
        skill_file = str(skill.path / "SKILL.md")
        console.print(f"  [dim]{_('skills.opening_editor', editor=editor)}[/dim]")
        subprocess.run([editor, skill_file])
        console.print(f"  [dim]{skill_file}[/dim]")
    except (KeyboardInterrupt, EOFError):
        console.print()
