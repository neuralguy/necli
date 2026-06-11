from __future__ import annotations

from .settings import get, set_value

# Семантические роли цветов
ROLES = (
    "accent",      # промпт, поиск, poll, thinking
    "success",     # response border, OK-статусы, agent mode
    "warning",     # shell, patch, предупреждения
    "error",       # ошибки
    "info",        # read, list, tree, cyan-элементы
    "magenta",     # ssh, subagent
    "purple",      # mode labels
    "muted",       # разделители, бордеры
    "dim_text",    # hint text, приглушённый текст
    "bar_filled",  # progress bar заполненная часть
    "bg_code",     # фон блоков кода
    "bg_output",   # фон вывода команд
    "bg_select",   # фон выделения в меню
)

ROLE_LABELS = {
    "accent": "Акцент (промпт, поиск)",
    "success": "Успех (ответ, ОК)",
    "warning": "Предупреждение (shell)",
    "error": "Ошибка",
    "info": "Информация (read, list)",
    "magenta": "SSH, субагент",
    "purple": "Метки режимов",
    "muted": "Приглушённый (разделители)",
    "dim_text": "Тусклый текст",
    "bar_filled": "Прогресс-бар",
    "bg_code": "Фон кода",
    "bg_output": "Фон вывода",
    "bg_select": "Фон выделения",
}

BUILTIN_THEMES: dict[str, dict[str, str]] = {
    "dracula": {
        "accent": "#4a9eff",
        "success": "#50fa7b",
        "warning": "#f1fa8c",
        "error": "#ff5555",
        "info": "#8be9fd",
        "magenta": "#ff79c6",
        "purple": "#bd93f9",
        "muted": "#444444",
        "dim_text": "#666666",
        "bar_filled": "#5b21b6",
        "bg_code": "#1a1a2e",
        "bg_output": "#0d1117",
        "bg_select": "#1e1e2e",
    },
    "monokai": {
        "accent": "#66d9ef",
        "success": "#a6e22e",
        "warning": "#e6db74",
        "error": "#f92672",
        "info": "#66d9ef",
        "magenta": "#fd5ff0",
        "purple": "#ae81ff",
        "muted": "#49483e",
        "dim_text": "#75715e",
        "bar_filled": "#ae81ff",
        "bg_code": "#272822",
        "bg_output": "#1e1f1c",
        "bg_select": "#3e3d32",
    },
    "catppuccin": {
        "accent": "#89b4fa",
        "success": "#a6e3a1",
        "warning": "#f9e2af",
        "error": "#f38ba8",
        "info": "#94e2d5",
        "magenta": "#f5c2e7",
        "purple": "#cba6f7",
        "muted": "#45475a",
        "dim_text": "#6c7086",
        "bar_filled": "#cba6f7",
        "bg_code": "#1e1e2e",
        "bg_output": "#181825",
        "bg_select": "#313244",
    },
    "nord": {
        "accent": "#88c0d0",
        "success": "#a3be8c",
        "warning": "#ebcb8b",
        "error": "#bf616a",
        "info": "#88c0d0",
        "magenta": "#b48ead",
        "purple": "#b48ead",
        "muted": "#434c5e",
        "dim_text": "#616e88",
        "bar_filled": "#5e81ac",
        "bg_code": "#2e3440",
        "bg_output": "#272c36",
        "bg_select": "#3b4252",
    },
    "gruvbox": {
        "accent": "#83a598",
        "success": "#b8bb26",
        "warning": "#fabd2f",
        "error": "#fb4934",
        "info": "#8ec07c",
        "magenta": "#d3869b",
        "purple": "#d3869b",
        "muted": "#504945",
        "dim_text": "#7c6f64",
        "bar_filled": "#d65d0e",
        "bg_code": "#282828",
        "bg_output": "#1d2021",
        "bg_select": "#3c3836",
    },
    "tokyo-night": {
        "accent": "#7aa2f7",
        "success": "#9ece6a",
        "warning": "#e0af68",
        "error": "#f7768e",
        "info": "#7dcfff",
        "magenta": "#bb9af7",
        "purple": "#bb9af7",
        "muted": "#3b4261",
        "dim_text": "#565f89",
        "bar_filled": "#7aa2f7",
        "bg_code": "#1a1b26",
        "bg_output": "#16161e",
        "bg_select": "#283457",
    },
    "solarized": {
        "accent": "#268bd2",
        "success": "#859900",
        "warning": "#b58900",
        "error": "#dc322f",
        "info": "#2aa198",
        "magenta": "#d33682",
        "purple": "#6c71c4",
        "muted": "#586e75",
        "dim_text": "#657b83",
        "bar_filled": "#6c71c4",
        "bg_code": "#002b36",
        "bg_output": "#00212b",
        "bg_select": "#073642",
    },
    "one-dark": {
        "accent": "#61afef",
        "success": "#98c379",
        "warning": "#e5c07b",
        "error": "#e06c75",
        "info": "#56b6c2",
        "magenta": "#c678dd",
        "purple": "#c678dd",
        "muted": "#3e4451",
        "dim_text": "#5c6370",
        "bar_filled": "#c678dd",
        "bg_code": "#282c34",
        "bg_output": "#21252b",
        "bg_select": "#2c313a",
    },
}

DEFAULT_THEME = "dracula"

_active: dict[str, str] | None = None


def _load_active() -> dict[str, str]:
    """Загружает активную тему из конфига."""
    global _active
    name = get("theme", DEFAULT_THEME)
    custom = get("theme_custom", {})

    if isinstance(name, str) and name in BUILTIN_THEMES:
        base = dict(BUILTIN_THEMES[name])
    else:
        base = dict(BUILTIN_THEMES[DEFAULT_THEME])

    if isinstance(custom, dict):
        for role in ROLES:
            if role in custom and isinstance(custom[role], str):
                base[role] = custom[role]

    _active = base
    return _active


def get_theme() -> dict[str, str]:
    """Возвращает словарь активной темы."""
    if _active is None:
        return _load_active()
    return _active


def t(role: str) -> str:
    """Быстрый доступ к цвету по роли. Основной API."""
    theme = get_theme()
    return theme.get(role, "#ffffff")


def set_theme(name: str) -> None:
    """Устанавливает встроенную тему."""
    global _active
    set_value("theme", name)
    set_value("theme_custom", {})
    _active = None


def set_custom_color(role: str, color: str) -> None:
    """Устанавливает кастомный цвет для роли поверх текущей темы."""
    global _active
    custom = get("theme_custom", {})
    if not isinstance(custom, dict):
        custom = {}
    custom[role] = color
    set_value("theme_custom", custom)
    _active = None


def reset_custom() -> None:
    """Сбрасывает кастомные цвета."""
    global _active
    set_value("theme_custom", {})
    _active = None


def get_active_theme_name() -> str:
    """Имя активной темы."""
    name = get("theme", DEFAULT_THEME)
    if isinstance(name, str) and name in BUILTIN_THEMES:
        return name
    return DEFAULT_THEME


def has_custom_overrides() -> bool:
    """Есть ли кастомные переопределения."""
    custom = get("theme_custom", {})
    return isinstance(custom, dict) and len(custom) > 0


def list_themes() -> list[str]:
    """Список имён встроенных тем."""
    return list(BUILTIN_THEMES.keys())
