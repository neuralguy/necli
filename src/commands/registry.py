# commands/registry.py

"""Единый реестр slash-команд.

Источник истины для:
  - диспетчера в commands/slash.py
  - группированного /help
  - автокомплита в ui/completer.py

Категории определяют группировку в /help и порядке в completion-меню.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    name: str                          # canonical, with leading "/"
    category: str                      # session | model | tools | display | misc
    desc_key: str                      # i18n key
    aliases: tuple[str, ...] = ()
    args_hint: str = ""                # rendered next to name, e.g. "[N]"
    completable: bool = True
    toggle_config_key: str = ""        # if set — команда трактуется как toggle,
                                       # completer показывает текущее состояние из config


# Категории в порядке вывода в /help.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("session",  "help.cat_session"),
    ("model",    "help.cat_model"),
    ("tools",    "help.cat_tools"),
    ("display",  "help.cat_display"),
    ("misc",     "help.cat_misc"),
)


COMMANDS: tuple[SlashCommand, ...] = (
    # ── session ─────────────────────────────────────────────
    SlashCommand("/new",         "session", "help.new"),
    SlashCommand("/branch",      "session", "help.branch"),
    SlashCommand("/commit",      "session", "help.commit"),
    SlashCommand("/sessions",    "session", "help.sessions"),
    SlashCommand("/history",     "session", "help.history",     args_hint="[N]"),
    SlashCommand("/compress",    "session", "help.compress"),
    SlashCommand("/decompress",  "session", "help.decompress"),
    SlashCommand("/undo",        "session", "help.undo",        args_hint="[N]"),
    SlashCommand("/reflect",     "session", "help.reflect"),
    SlashCommand("/plan",        "session", "help.plan"),

    # ── model ───────────────────────────────────────────────
    SlashCommand("/api",         "model",   "help.api"),
    SlashCommand("/models",      "model",   "help.models"),
    SlashCommand("/params",      "model",   "help.params"),

    # ── tools ───────────────────────────────────────────────
    SlashCommand("/cd",          "tools",   "help.cd",          args_hint="PATH"),
    SlashCommand("/permissions", "tools",   "help.permissions"),
    SlashCommand("/mcp",         "tools",   "help.mcp"),
    SlashCommand("/lsp",         "tools",   "help.lsp"),
    SlashCommand("/skills",      "tools",   "help.skills"),
    SlashCommand("/agents",      "tools",   "help.agents"),
    SlashCommand("/ssh",         "tools",   "help.ssh"),

    # ── display ─────────────────────────────────────────────
    SlashCommand("/themes",      "display", "help.themes"),
    SlashCommand("/lang",        "display", "help.lang"),
    SlashCommand("/think",       "display", "help.think",       toggle_config_key="think_enabled"),
    SlashCommand("/tool_format", "display", "help.tool_format", toggle_config_key="tool_format_force_native"),

    # ── misc ────────────────────────────────────────────────
    SlashCommand("/help",        "misc",    "help.help"),
    SlashCommand("/stats",       "misc",    "help.stats",       args_hint="[N]"),
    SlashCommand("/insights",    "misc",    "help.insights"),
    SlashCommand("/copy",        "misc",    "help.copy",        args_hint="[N]"),
    SlashCommand("/tg",          "misc",    "help.tg"),
)


# Lookup: name/alias → SlashCommand
_BY_NAME: dict[str, SlashCommand] = {}
for _c in COMMANDS:
    _BY_NAME[_c.name] = _c
    for _a in _c.aliases:
        _BY_NAME[_a] = _c


def lookup(name: str) -> SlashCommand | None:
    """Resolve command by canonical name or alias."""
    return _BY_NAME.get(name)


def by_category() -> list[tuple[str, str, list[SlashCommand]]]:
    """Список (cat_id, cat_desc_key, [commands]) в порядке CATEGORIES."""
    groups: dict[str, list[SlashCommand]] = {cat: [] for cat, _ in CATEGORIES}
    for c in COMMANDS:
        groups.setdefault(c.category, []).append(c)
    return [(cat, key, groups.get(cat, [])) for cat, key in CATEGORIES]