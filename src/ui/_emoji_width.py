"""Monkey-patch rich.cells, чтобы emoji считались как 1 cell.

ПРОБЛЕМА. Rich (через таблицы wcwidth/Unicode) считает emoji-кодпоинты как
2-cell wide символы. Большинство современных терминалов так и рендерят
(WezTerm, Kitty, iTerm2, Windows Terminal). Но во многих ситуациях шрифт
терминала рендерит emoji как 1 cell:
  - не-emoji NerdFont без emoji-патчей,
  - старый xterm/urxvt,
  - tmux без `set -g allow-passthrough on` и/или с monospace-only шрифтом,
  - Linux console (TTY), ssh в окружения без emoji-шрифта.

В таких случаях Rich резервирует под emoji 2 колонки, а физически рисуется
1 — правая граница панели сползает влево на (кол-во emoji в строке) позиций.
Видно особенно в блоках create_file/write_file/shell с эмодзи в коде.

ФИКС. Если включена опция `emoji_width=1` (config) или переменная окружения
`NECLI_EMOJI_WIDTH=1` — патчим `rich.cells.get_character_cell_size` так,
чтобы для emoji-диапазонов он возвращал 1 вместо 2. Также сбрасываем
lru_cache'и `get_character_cell_size` и `cached_cell_len`.

По умолчанию (значение != 1) ничего не делаем — поведение Rich сохраняется
для пользователей с правильным emoji-шрифтом.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Основные emoji-диапазоны Unicode, которые Rich (по wcwidth) считает за 2 cell.
# Список не исчерпывающий, но покрывает все типичные emoji + symbols, видимые
# в выводе CLI (✓ ✗ ✨ 🎯 🚀 📖 📝 🔧 ⏺ и т.п.).
_EMOJI_RANGES: list[tuple[int, int]] = [
    (0x203C, 0x203C),   # ‼
    (0x2049, 0x2049),   # ⁉
    (0x2122, 0x2122),   # ™
    (0x2139, 0x2139),   # ℹ
    (0x2194, 0x2199),   # ↔ ↕ ↖ ↗ ↘ ↙
    (0x21A9, 0x21AA),   # ↩ ↪
    (0x231A, 0x231B),   # ⌚ ⌛
    (0x2328, 0x2328),   # ⌨
    (0x23CF, 0x23CF),   # ⏏
    (0x23E9, 0x23F3),   # ⏩ … ⏳
    (0x23F8, 0x23FA),   # ⏸ ⏹ ⏺
    (0x24C2, 0x24C2),   # Ⓜ
    (0x25AA, 0x25AB),   # ▪ ▫
    (0x25B6, 0x25B6),   # ▶
    (0x25C0, 0x25C0),   # ◀
    (0x25FB, 0x25FE),   # ◻ ◼ ◽ ◾
    (0x2600, 0x27BF),   # Misc symbols, dingbats (✓ ✗ ✨ ❌ ❓ ➕ ➖ …)
    (0x2934, 0x2935),   # ⤴ ⤵
    (0x2B05, 0x2B07),   # ⬅ ⬆ ⬇
    (0x2B1B, 0x2B1C),   # ⬛ ⬜
    (0x2B50, 0x2B50),   # ⭐
    (0x2B55, 0x2B55),   # ⭕
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x1F000, 0x1FFFF),  # Все плоскости emoji (SMP): 🀀–🟿
    (0x1F100, 0x1F1FF),  # Enclosed alphanumeric supplement (региональные)
    (0x1F200, 0x1F2FF),  # Enclosed ideographic supplement
    (0x1F300, 0x1F5FF),  # Misc symbols and pictographs
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F680, 0x1F6FF),  # Transport and map
    (0x1F700, 0x1F77F),  # Alchemical
    (0x1F780, 0x1F7FF),  # Geometric shapes ext
    (0x1F800, 0x1F8FF),  # Supplemental arrows-C
    (0x1F900, 0x1F9FF),  # Supplemental symbols and pictographs
    (0x1FA00, 0x1FA6F),  # Chess symbols
    (0x1FA70, 0x1FAFF),  # Symbols and pictographs ext-A
]


def _is_emoji_codepoint(cp: int) -> bool:
    for start, end in _EMOJI_RANGES:
        if cp < start:
            return False
        if cp <= end:
            return True
    return False


_PATCHED = False


def _emoji_width_enabled() -> bool:
    env = os.environ.get("NECLI_EMOJI_WIDTH", "").strip()
    if env:
        return env == "1"
    try:
        from config.settings import get as cfg_get
        val = cfg_get("emoji_width", 0)
        return int(val) == 1
    except Exception:
        return False


def apply_emoji_width_patch() -> None:
    """Применить патч, если включено в конфиге/env. Идемпотентно."""
    global _PATCHED
    if _PATCHED:
        return
    if not _emoji_width_enabled():
        return
    try:
        import rich.cells as rc
    except ImportError:
        return

    orig_get_size = rc.get_character_cell_size

    def patched_get_character_cell_size(character: str, unicode_version: str = "auto") -> int:
        if character and _is_emoji_codepoint(ord(character)):
            return 1
        return orig_get_size(character, unicode_version)

    # Сбрасываем lru_cache на оригинале (он мог быть прогрет старыми значениями)
    try:
        orig_get_size.cache_clear()
    except AttributeError:
        pass

    rc.get_character_cell_size = patched_get_character_cell_size

    # cached_cell_len и cell_len используют get_character_cell_size через _cell_len
    # → достаточно очистить их lru_cache.
    try:
        rc.cached_cell_len.cache_clear()
    except AttributeError:
        pass

    _PATCHED = True
    logger.info("emoji_width patch applied: emoji counted as 1 cell")
