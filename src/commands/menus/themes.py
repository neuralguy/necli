"""Меню /themes: список тем с палитрой и живым превью под ним.

Два исправления по жалобам заказчика.

* **Выбор темы закрывает меню.** Применение темы — терминальное действие: цель
  команды достигнута, держать виджет открытым незачем. Навигационные пункты
  («настроить») меню не закрывают.
* **Превью — внутри виджета, а не в scrollback**, и кэшируется по палитре и
  ширине: Rich-рендер превью на каждом кадре тикера был лишней работой 20 раз
  в секунду.
"""

import re

import config
from agent.theme_preview import render_theme_preview
from commands.menus._style import card_menu
from config.i18n import t as _
from config.themes import (
    BUILTIN_THEMES,
    FALLBACK,
    ROLE_LABELS,
    ROLES,
    get_active_theme_name,
    get_theme,
    has_custom_overrides,
    list_themes,
    reset_custom,
    set_custom_color,
    set_theme,
)
from ui import overlays
from ui.menu import render_width

_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

_SWATCH_ROLES = ("accent", "success", "warning", "error", "info", "magenta", "purple")

#: Превью зависит только от палитры и ширины, поэтому кэшируем его по этой паре.
_preview_cache: dict[tuple, str] = {}


def _preview(colors: dict, key: str, width: int) -> str:
    """Превью темы целиком.

    Отступ такой же, как у строк списка: превью — продолжение виджета, а не
    отдельный блок, и левый край должен совпадать. Обрезку по высоте делает
    виджет (превью приоритетнее списка), а не этот хелпер, — иначе на узких
    экранах превью резалось первым, оставляя в меню один блок Shell.
    """
    cached = _preview_cache.get((key, width))
    if cached is None:
        body = render_theme_preview(colors, width=width).rstrip("\n")
        cached = "\n".join("  " + ln for ln in body.split("\n"))
        _preview_cache[(key, width)] = cached
    return "\n".join(["", *cached.split("\n")])


def _preview_width() -> int:
    return max(30, min(76, render_width() - 6))


async def themes_interactive():
    """Список тем. Выбор темы применяет её и закрывает меню."""
    while True:
        current = get_active_theme_name()
        custom = has_custom_overrides()
        theme_names = list_themes()

        initial = 0
        items = []
        for i, name in enumerate(theme_names):
            if name == current:
                initial = i
            colors = BUILTIN_THEMES[name]
            items.append({
                "label": name,
                "swatch": [colors[role] for role in _SWATCH_ROLES],
                "badge": ("● " + _("themes.current")
                          + (" " + _("themes.plus_custom") if custom else "")
                          if name == current else ""),
                "badge_style": "success",
            })
        custom_idx = len(theme_names)
        items.append({"label": _("themes.customize"), "hint": _("themes.customize_hint")})
        if custom:
            items.append({"label": _("themes.reset"), "hint": _("themes.reset_hint")})

        def footer(sel: int, names=theme_names) -> str:
            # Палитра для превью: подсвеченная тема либо актуальная (на
            # служебных пунктах «настроить» / «сбросить»).
            if sel < len(names):
                colors, key = BUILTIN_THEMES[names[sel]], names[sel]
            else:
                colors, key = get_theme(), "current"
            return _preview(colors, key, _preview_width())

        choice = await card_menu(
            items,
            title=_("themes.title"),
            current=initial,
            hint_text=_("themes.hint_apply"),
            footer_fn=footer,
            expand=True,
        )

        if choice is None:
            return

        if custom and choice == custom_idx + 1:
            reset_custom()
            _preview_cache.clear()
            return

        if choice == custom_idx:
            await _theme_customize()
            continue

        chosen_name = theme_names[choice]
        if chosen_name != current or custom:
            set_theme(chosen_name)
        return


# Пусто = отказ от правки; иначе строго #rrggbb. Проверка та же, что стояла
# после ввода, но теперь она внутри оверлея и правится на месте.
def _validate_hex(raw: str) -> str | None:
    if not raw:
        return None
    return None if _HEX_RE.match(raw) else _("themes.cust_invalid")


async def _theme_customize():
    """Правка отдельных ролей палитры. Хаб настроек — не закрывается по правке."""
    while True:
        current_colors = get_theme()
        custom_overrides = config.get("theme_custom", {})
        if not isinstance(custom_overrides, dict):
            custom_overrides = {}

        roles_list = list(ROLES)
        items = []
        for role in roles_list:
            color = current_colors.get(role, FALLBACK)
            items.append({
                "icon": "✎" if role in custom_overrides else " ",
                "icon_style": "warning",
                "label": ROLE_LABELS.get(role, role),
                "swatch": [color],
                "badge": color,
                "badge_style": "dim",
            })
        items.append({"label": _("common.back")})

        def footer(_sel: int, colors=current_colors) -> str:
            return _preview(colors, "current", _preview_width())

        choice = await card_menu(
            items,
            title=_("themes.cust_title"),
            facts=[_("themes.cust_subtitle")],
            hint_text=_("themes.hint_edit"),
            footer_fn=footer,
            expand=True,
        )

        if choice is None or choice == len(roles_list):
            return

        role = roles_list[choice]
        role_label = ROLE_LABELS.get(role, role)
        current_val = current_colors.get(role, FALLBACK)

        new_val = await overlays.ask_text(
            f"{role_label} · {_('themes.cust_new_color')} ({current_val}):",
            validate=_validate_hex,
        )
        if not new_val:
            continue

        set_custom_color(role, new_val)
        _preview_cache.clear()
