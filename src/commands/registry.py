# commands/registry.py

"""Единый реестр slash-команд.

Источник истины для диспетчера, /help и автокомплита.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str
    category: str
    desc_key: str
    aliases: tuple[str, ...] = ()
    args_hint: str = ""
    completable: bool = True
    toggle_config_key: str = ""
    action: bool = False
    immediate: bool = False


CATEGORIES: tuple[tuple[str, str], ...] = (
    ("session", "help.cat_session"),
    ("model", "help.cat_model"),
    ("tools", "help.cat_tools"),
    ("display", "help.cat_display"),
    ("misc", "help.cat_misc"),
)


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/new", "session", "help.new", action=True),
    SlashCommand("/branch", "session", "help.branch", action=True),
    SlashCommand("/commit", "session", "help.commit", args_hint="[hint]", action=True),
    SlashCommand("/sessions", "session", "help.sessions"),
    SlashCommand("/compress", "session", "help.compress", action=True),
    SlashCommand("/reflect", "session", "help.reflect", action=True),
    SlashCommand("/api", "model", "help.api", immediate=True),
    SlashCommand("/models", "model", "help.models", immediate=True),
    SlashCommand("/autoprune", "model", "help.autoprune", immediate=True),
    SlashCommand(
        "/proxy", "model", "help.proxy", args_hint="[URL|off]", action=True, immediate=True
    ),
    SlashCommand("/permissions", "tools", "help.permissions", immediate=True),
    SlashCommand("/tools", "tools", "help.tools"),
    SlashCommand("/skills", "tools", "help.skills", immediate=True),
    SlashCommand("/agents", "tools", "help.agents", immediate=True),
    SlashCommand("/themes", "display", "help.themes", immediate=True),
    SlashCommand("/plan", "display", "help.plan", immediate=True),
    SlashCommand(
        "/think",
        "display",
        "help.think",
        args_hint="[on|off]",
        toggle_config_key="think_enabled",
        action=True,
    ),
    SlashCommand(
        "/tool_format",
        "display",
        "help.tool_format",
        args_hint="[on|off]",
        toggle_config_key="tool_format_force_native",
        action=True,
    ),
    SlashCommand("/help", "misc", "help.help", immediate=True),
    SlashCommand("/settings", "misc", "help.settings", immediate=True),
    SlashCommand("/stats", "misc", "help.stats", args_hint="[N]", immediate=True),
    SlashCommand("/insights", "misc", "help.insights", aliases=("/insight",)),
    SlashCommand("/copy", "misc", "help.copy", args_hint="[N]", action=True),
    SlashCommand("/tg", "misc", "help.tg", immediate=True),
)


_BY_NAME: dict[str, SlashCommand] = {}
for _c in COMMANDS:
    _BY_NAME[_c.name] = _c
    for _a in _c.aliases:
        _BY_NAME[_a] = _c


def lookup(name: str) -> SlashCommand | None:
    return _BY_NAME.get(name)


IMMEDIATE_SLASH: frozenset[str] = frozenset(
    command.name for command in COMMANDS if command.immediate
)


def is_immediate(name: str) -> bool:
    command = lookup(name)
    return command is not None and command.immediate


def command_label(name: str, *, include_action: bool = False) -> str:
    """Return the canonical command label rendered from registry metadata."""
    command = lookup(name)
    if command is None:
        return name
    label = command.name
    if command.args_hint:
        label = f"{label} {command.args_hint}"
    if include_action and command.action:
        label += " · act"
    return label


def by_category() -> list[tuple[str, str, list[SlashCommand]]]:
    groups: dict[str, list[SlashCommand]] = {cat: [] for cat, _ in CATEGORIES}
    for c in COMMANDS:
        groups.setdefault(c.category, []).append(c)
    return [(cat, key, groups.get(cat, [])) for cat, key in CATEGORIES]
