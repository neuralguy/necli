"""commands/registry.py — реестр slash-команд: lookup, /help, автокомплит."""

import pytest

from commands.registry import (
    CATEGORIES,
    COMMANDS,
    by_category,
    lookup,
)
from config.i18n import t

class TestCommandsStructure:
    def test_non_empty(self):
        assert len(COMMANDS) > 0

    def test_all_names_have_leading_slash(self):
        for c in COMMANDS:
            assert c.name.startswith("/"), f"{c.name} missing leading slash"

    def test_names_unique(self):
        names = [c.name for c in COMMANDS]
        assert len(names) == len(set(names)), "duplicate command names"

    def test_aliases_unique_and_no_collision_with_names(self):
        names = {c.name for c in COMMANDS}
        seen: set[str] = set()
        for c in COMMANDS:
            for a in c.aliases:
                assert a.startswith("/"), f"alias {a} missing leading slash"
                assert a not in names, f"alias {a} collides with a command name"
                assert a not in seen, f"alias {a} duplicated"
                seen.add(a)

    def test_every_category_is_known(self):
        valid = {cat for cat, _ in CATEGORIES}
        for c in COMMANDS:
            assert c.category in valid, f"{c.name} has unknown category {c.category}"

    def test_command_is_frozen(self):
        c = COMMANDS[0]
        with pytest.raises(Exception):
            c.name = "/mutated"  # type: ignore[misc]

class TestHelpTextCompleteness:
    def test_every_command_desc_key_resolves(self):
        """Каждый desc_key должен быть переведён (t не возвращает сам ключ)."""
        missing = [c.name for c in COMMANDS if t(c.desc_key) == c.desc_key]
        assert not missing, f"commands without help text: {missing}"

    def test_desc_keys_follow_help_prefix(self):
        for c in COMMANDS:
            assert c.desc_key.startswith("help."), f"{c.name}: {c.desc_key}"

    def test_every_category_desc_key_resolves(self):
        missing = [key for _, key in CATEGORIES if t(key) == key]
        assert not missing, f"categories without help text: {missing}"

    def test_help_text_non_empty(self):
        for c in COMMANDS:
            assert t(c.desc_key).strip(), f"{c.name} help text is empty"

class TestLookup:
    def test_lookup_by_canonical_name(self):
        cmd = lookup("/help")
        assert cmd is not None
        assert cmd.name == "/help"

    def test_lookup_unknown_returns_none(self):
        assert lookup("/definitely_not_a_command") is None

    def test_lookup_returns_same_instance_as_in_commands(self):
        cmd = lookup("/new")
        assert cmd is not None
        assert cmd in COMMANDS

    def test_lookup_every_command(self):
        for c in COMMANDS:
            assert lookup(c.name) is c

    def test_lookup_every_alias(self):
        for c in COMMANDS:
            for a in c.aliases:
                assert lookup(a) is c

class TestByCategory:
    def test_order_matches_categories(self):
        groups = by_category()
        order = [cat for cat, _, _ in groups]
        assert order == [cat for cat, _ in CATEGORIES]

    def test_returns_desc_key_per_category(self):
        groups = by_category()
        for cat, key, _cmds in groups:
            expected = dict(CATEGORIES)[cat]
            assert key == expected

    def test_all_commands_accounted_for(self):
        groups = by_category()
        collected = [c for _, _, cmds in groups for c in cmds]
        assert len(collected) == len(COMMANDS)
        assert set(collected) == set(COMMANDS)

    def test_each_group_commands_match_their_category(self):
        for cat, _key, cmds in by_category():
            for c in cmds:
                assert c.category == cat