"""Интерактивная многоуровневая справка.

Уровень 1: выбор раздела (↑↓, Enter, Esc).
Уровень 2: страницы раздела. Слева — информация, справа — примеры.
           Навигация ←→ между страницами, Esc — назад к разделам.

Всё рисуется внутри нижней зоны Shell (Overlay), без рамок,
на полную высоту. Стиль — общий с остальными виджетами (ui/overlays).
"""

from commands.menus.help.content import SECTIONS
from commands.menus.help.overlays import (
    HelpPagesOverlay,
    HelpSectionsOverlay,
    help_interactive,
)

__all__ = [
    "SECTIONS",
    "HelpPagesOverlay",
    "HelpSectionsOverlay",
    "help_interactive",
]
