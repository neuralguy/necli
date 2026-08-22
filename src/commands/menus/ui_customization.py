"""Detailed interactive editor for config/ui.json values."""

from __future__ import annotations

from typing import Any

from commands.menus._style import card_menu, facts_line
from config.i18n import t as _
from config.ui import DEFAULTS, ui
from ui import overlays

_SKIP_GROUPS = {"_comment", "_help"}


def _groups() -> list[str]:
    return [
        key
        for key, value in DEFAULTS.items()
        if key not in _SKIP_GROUPS and isinstance(value, dict)
    ]


def _flatten(value: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            rows.extend(_flatten(child, path))
        else:
            rows.append((path, child))
    return rows


def _short(value: Any, limit: int = 42) -> str:
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value)
    elif isinstance(value, bool):
        text = "on" if value else "off"
    else:
        text = str(value)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse(raw: str, template: Any) -> Any:
    if isinstance(template, bool):
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        raise ValueError("boolean")
    if isinstance(template, int) and not isinstance(template, bool):
        return int(raw.strip())
    if isinstance(template, float):
        return float(raw.strip())
    if isinstance(template, list):
        parts = [part.strip() for part in raw.split(",")]
        if template and all(
            isinstance(item, int) and not isinstance(item, bool) for item in template
        ):
            return [int(part) for part in parts if part]
        if template and all(isinstance(item, float) for item in template):
            return [float(part) for part in parts if part]
        return [part for part in parts if part or len(parts) == 1]
    return raw


async def _edit(path: str, default: Any) -> None:
    current = ui.get(path, default)
    if path == "diff.mode":
        modes = ["inline", "side_by_side"]
        choice = await card_menu(
            [
                {"label": _("display.diff_inline"), "active": current == "inline"},
                {
                    "label": _("display.diff_side_by_side"),
                    "active": current == "side_by_side",
                },
                {"label": _("common.back")},
            ],
            title=_("display.patch_diff"),
        )
        if choice is not None and choice < 2:
            ui.set(path, modes[choice])
        return
    if isinstance(default, bool):
        ui.set(path, not bool(current))
        return

    shown = (
        ", ".join(str(item) for item in current)
        if isinstance(current, list)
        else str(current)
    )
    raw = await overlays.ask_text(f"{path}:", default=shown)
    if raw is None:
        return
    try:
        ui.set(path, _parse(raw, default))
    except (TypeError, ValueError):
        # Keep the editor open without writing malformed numeric/list values.
        return


async def _group_editor(group: str) -> None:
    defaults = DEFAULTS[group]
    leaves = _flatten(defaults)
    while True:
        items = [
            {
                "label": path,
                "hint": f"- {_short(ui.get(f'{group}.{path}', default))}",
                "active": ui.get(f"{group}.{path}", default) != default,
            }
            for path, default in leaves
        ]
        items.extend(
            [
                {"label": _("display.ui_reset_group"), "action": True},
                {"label": _("common.back")},
            ]
        )
        choice = await card_menu(
            items,
            title=f"{_('display.ui_customization')} · {group}",
            facts=[facts_line(_("display.ui_values"), str(len(leaves)))],
        )
        if choice is None or choice == len(leaves) + 1:
            return
        if choice == len(leaves):
            for path, _default in leaves:
                ui.reset(f"{group}.{path}")
            continue
        path, default = leaves[choice]
        await _edit(f"{group}.{path}", default)


async def ui_customization_interactive() -> None:
    groups = _groups()
    while True:
        choice = await card_menu(
            [
                {"label": group, "hint": f"- {len(_flatten(DEFAULTS[group]))}"}
                for group in groups
            ]
            + [{"label": _("common.back")}],
            title=_("display.ui_customization"),
        )
        if choice is None or choice == len(groups):
            return
        await _group_editor(groups[choice])
