"""Меню /skills: список навыков и карточка навыка.

Карточка (описание, путь, начало текста) раньше печаталась в scrollback на
каждом витке цикла и дублировалась там после каждого нажатия. Теперь она —
часть виджета; события показываются динамическим notice.
"""

from commands.menus._editor import editor_command, open_in_editor
from commands.menus._style import card_menu, confirm_delete
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
from ui import overlays


async def skills_interactive():
    while True:
        all_skills = list_skills()
        if not all_skills:
            choice = await card_menu(
                [{"label": _("skills.create")}],
                title=_("skills.title"),
                facts=[_("skills.no_skills", dir=get_skills_dir())],
            )
            if choice == 0:
                await _skill_create_interactive()
                continue
            return

        active_n = sum(1 for s in all_skills if is_skill_active(s.name))
        items = [
            {
                "icon": "●" if is_skill_active(s.name) else "·",
                "icon_style": "success" if is_skill_active(s.name) else "dim",
                "label": s.name,
                "hint": s.description or "—",
                "badge": _("skills.active") if is_skill_active(s.name) else "",
                "badge_style": "success",
            }
            for s in all_skills
        ]
        items.append({"label": _("skills.create"), "hint": str(get_skills_dir())})

        choice = await card_menu(
            items,
            title=_("skills.title"),
            facts=[f"{len(all_skills)} · {active_n} {_('skills.active')}"],
        )
        if choice is None:
            return
        if choice == len(all_skills):
            await _skill_create_interactive()
            continue

        skill = all_skills[choice]
        action = await _skill_detail_menu(skill)
        if action == "back":
            continue
        return


async def _skill_detail_menu(skill):
    while True:
        active = is_skill_active(skill.name)

        # Начало текста навыка — часть карточки. Больше строк, чем влезает,
        # CardMenu отбросит сам: список действий важнее превью.
        body_lines = [ln for ln in skill.body[:400].splitlines() if ln.strip()][:6]
        toggle = _("skills.toggle_disable") if active else _("skills.toggle_enable")
        actions = [
            {"label": toggle,
             "hint": _("skills.toggle_hint_off") if active else _("skills.toggle_hint_on"),
             "icon": "○" if active else "●",
             "icon_style": "warning" if active else "success"},
            {"label": _("skills.edit"), "hint": editor_command(), "icon": "✎",
             "icon_style": "dim"},
            {"label": _("api.delete"), "hint": _("api.delete_permanent"), "icon": "✗",
             "icon_style": "error"},
            {"label": _("common.back"), "icon": " "},
        ]
        choice = await card_menu(
            actions,
            title=skill.name,
            status=_("skills.active") if active else _("skills.inactive"),
            status_style="success" if active else "muted",
            facts=[
                skill.description or _("skills.no_description"),
                str(skill.path / "SKILL.md"),
                *body_lines,
            ],
        )

        if choice is None or choice == 3:
            return "back"

        if choice == 0:
            if active:
                deactivate_skill(skill.name)
            else:
                activate_skill(skill.name)
            return "back"

        if choice == 1:
            skill_file = str(skill.path / "SKILL.md")
            try:
                await open_in_editor(skill_file)
                skill = load_skill(skill.name)
                if skill is None:
                    return "back"
            except Exception:
                pass
            continue

        if choice == 2:
            if await confirm_delete(_("skills.delete_q", name=skill.name)):
                remove_skill(skill.name)
                return "back"
            continue


async def _skill_create_interactive():
    name = await overlays.ask_text(f"{_('skills.field_name')}:")
    if not name:
        return
    desc = await overlays.ask_text(f"{_('skills.field_description')}:")
    if desc is None:
        return  # esc в любом поле отменяет создание, как прежний Ctrl+C
    skill = create_skill(name, desc or name, f"# {name}\n\nSkill instructions here.")
    await open_in_editor(str(skill.path / "SKILL.md"))
