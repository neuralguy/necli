"""Меню /agents: пресеты субагентов и карточка пресета.

Как и в /skills, карточка пресета живёт внутри виджета: печатать её в
scrollback на каждом витке цикла означало плодить там копии.
"""

from agent.agent_presets import (
    create_preset,
    get_agents_dir,
    list_presets,
    load_preset,
    remove_preset,
)
from commands.menus._editor import editor_command, open_in_editor
from commands.menus._style import card_menu, confirm_delete
from config.i18n import t as _
from ui import overlays


async def agents_interactive():
    while True:
        presets = list_presets()
        if not presets:
            choice = await card_menu(
                [{"label": "Create preset…"}],
                title="Agent presets",
                facts=[f"No agent presets yet · {get_agents_dir()}"],
            )
            if choice == 0:
                await _preset_create_interactive()
                continue
            return

        # У AgentPreset есть только name/description/path/model — прежний код
        # читал ещё .mode и .tools, которых в модели нет, и меню падало
        # AttributeError на первом же пресете.
        items = [
            {
                "label": p.name,
                "hint": p.description or "—",
                "badge": p.model or "",
                "badge_style": "accent",
            }
            for p in presets
        ]
        items.append({"label": "Create preset…", "hint": str(get_agents_dir())})

        choice = await card_menu(items, title="Agent presets",
                                 facts=[f"{len(presets)} preset(s)"])
        if choice is None:
            return
        if choice == len(presets):
            await _preset_create_interactive()
            continue

        action = await _preset_detail_menu(presets[choice])
        if action == "back":
            continue
        return


async def _preset_detail_menu(preset):
    while True:
        body_lines = [ln for ln in preset.body[:400].splitlines() if ln.strip()][:6]
        actions = [
            {"label": "Edit", "hint": editor_command(), "icon": "✎", "icon_style": "dim"},
            {"label": _("api.delete"), "hint": _("api.delete_permanent"), "icon": "✗",
             "icon_style": "error"},
            {"label": _("common.back"), "icon": " "},
        ]
        choice = await card_menu(
            actions,
            title=preset.name,
            status=preset.model or "",
            status_style="accent",
            facts=[
                preset.description or "—",
                str(preset.path / "AGENT.md"),
                *body_lines,
            ],
        )

        if choice is None or choice == 2:
            return "back"

        if choice == 0:
            preset_file = str(preset.path / "AGENT.md")
            try:
                await open_in_editor(preset_file)
                reloaded = load_preset(preset.name)
                if reloaded is None:
                    return "back"
                preset = reloaded
            except Exception:
                pass
            continue

        if choice == 1:
            if await confirm_delete(f"Delete preset '{preset.name}'?"):
                remove_preset(preset.name)
                return "back"
            continue


async def _preset_create_interactive():
    name = await overlays.ask_text("Name:")
    if not name:
        return
    desc = await overlays.ask_text("Description:")
    if desc is None:
        return  # esc в любом поле отменяет создание, как прежний Ctrl+C
    model = await overlays.ask_text("Model (optional):")
    if model is None:
        return
    body = f"Your ROLE is {name}.\n\nDescribe the role instructions here."
    preset = create_preset(name, desc or name, body, model=model or None)
    await open_in_editor(str(preset.path / "AGENT.md"))
