"""Интерактивное управление долговременной памятью из ``/settings``."""

from __future__ import annotations

from commands.menus._editor import open_in_editor
from commands.menus._style import card_menu, confirm_delete, facts_line
from config.i18n import t as _
from config.settings import get, set_value
from memory import delete_memory, scan_memories
from tools._paths import get_working_dir


def memory_enabled() -> bool:
    return bool(get("memory_enabled", True))


def set_memory_enabled(enabled: bool) -> None:
    set_value("memory_enabled", bool(enabled))


def _records(working_dir: str) -> list[tuple[str, object]]:
    records: list[tuple[str, object]] = []
    seen = set()
    for scope in ("global", "project"):
        for record in scan_memories(working_dir, scope=scope):
            if record.path in seen:
                continue
            seen.add(record.path)
            records.append((scope, record))
    return records


def _record_item(scope: str, record) -> dict:
    first = (record.body.splitlines()[0] if record.body else _("memory.empty"))[:100]
    return {
        "icon": "●",
        "icon_style": "accent",
        "label": record.name,
        "hint": f"{_(f'memory.{scope}')} · {record.type} · {first}",
    }


async def _record_action(scope: str, record, working_dir: str) -> bool:
    action = await card_menu(
        [
            {"label": _("memory.edit"), "hint": _("memory.edit_hint"), "action": True},
            {
                "label": _("memory.delete"),
                "hint": _("memory.delete_hint"),
                "action": True,
            },
            {"label": _("common.back")},
        ],
        title=record.name,
        status=_(f"memory.{scope}"),
        facts=[
            facts_line(_("memory.type"), record.type),
            *(record.body.splitlines()[:5] or [_("memory.empty")]),
        ],
        expand=True,
    )
    if action == 0:
        await open_in_editor(str(record.path))
    elif action == 1 and await confirm_delete(_("memory.delete_q", name=record.name)):
        delete_memory(record.name, working_dir=working_dir, scope=scope)
        return True
    return False


async def memory_interactive(working_dir: str | None = None) -> None:
    working_dir = working_dir or get_working_dir()
    while True:
        records = _records(working_dir)
        items = [
            {
                "label": _("memory.disable")
                if memory_enabled()
                else _("memory.enable"),
                "hint": _("memory.toggle_hint"),
                "icon": "●" if memory_enabled() else "○",
                "icon_style": "success" if memory_enabled() else "muted",
                "action": True,
            }
        ]
        items.extend(_record_item(scope, record) for scope, record in records)
        items.append({"label": _("common.back")})
        choice = await card_menu(
            items,
            title=_("memory.title"),
            status=_("memory.enabled") if memory_enabled() else _("memory.disabled"),
            status_style="success" if memory_enabled() else "muted",
            facts=[facts_line(_("memory.records"), str(len(records)))],
        )
        if not isinstance(choice, int) or choice == len(items) - 1:
            return
        if choice == 0:
            set_memory_enabled(not memory_enabled())
            continue
        scope, record = records[choice - 1]
        await _record_action(scope, record, working_dir)


__all__ = ["memory_enabled", "memory_interactive", "set_memory_enabled"]
