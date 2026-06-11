"""config/themes.py — система тем и кастомных цветов."""

import pytest

from config import themes


@pytest.fixture(autouse=True)
def _isolated(isolated_data):
    themes._active = None
    yield
    themes._active = None


class TestGetTheme:
    def test_default_is_dracula(self):
        assert themes.get_active_theme_name() == "dracula"

    def test_returns_all_roles(self):
        theme = themes.get_theme()
        for role in themes.ROLES:
            assert role in theme

    def test_t_helper(self):
        assert themes.t("accent").startswith("#")


class TestSetTheme:
    def test_switches_builtin(self):
        themes.set_theme("monokai")
        assert themes.get_active_theme_name() == "monokai"
        # цвета поменялись
        assert themes.t("accent") == "#66d9ef"

    def test_unknown_falls_back_to_default(self):
        themes.set_theme("nonexistent-theme-name")
        # имя сохраняется, но get_active_theme_name возвращает default
        assert themes.get_active_theme_name() == "dracula"


class TestCustomColor:
    def test_overrides_role(self):
        themes.set_custom_color("accent", "#ff00ff")
        assert themes.t("accent") == "#ff00ff"

    def test_has_overrides(self):
        themes.set_custom_color("accent", "#abc")
        assert themes.has_custom_overrides() is True

    def test_reset_custom(self):
        themes.set_custom_color("accent", "#ff00ff")
        themes.reset_custom()
        assert themes.has_custom_overrides() is False
        # вернулись к стандартному dracula
        assert themes.t("accent") == "#4a9eff"


class TestSetThemeResetsCustom:
    def test_switch_clears_overrides(self):
        themes.set_custom_color("accent", "#ff00ff")
        themes.set_theme("nord")
        assert themes.has_custom_overrides() is False


class TestListThemes:
    def test_returns_builtins(self):
        names = themes.list_themes()
        assert "dracula" in names
        assert "monokai" in names
        assert "nord" in names


class TestBuiltinsCoverage:
    def test_all_builtins_have_all_roles(self):
        for name, theme in themes.BUILTIN_THEMES.items():
            for role in themes.ROLES:
                assert role in theme, f"theme {name} missing role {role}"
                assert theme[role].startswith("#"), f"{name}.{role} = {theme[role]} (not hex)"