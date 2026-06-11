"""Канонический helper для поиска pandoc.

Кэшируется на процесс через lru_cache. Используется в:
  - tools/file_ops/docx_writer.py (write)
  - tools/file_readers.py (read через _read_docx_via_pandoc)
"""

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def find_pandoc() -> Optional[str]:
    """Возвращает абсолютный путь к pandoc или None. Кэшируется на процесс."""
    found = shutil.which("pandoc")
    if found:
        return found
    if sys.platform != "win32":
        return None

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Pandoc" / "pandoc.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Pandoc" / "pandoc.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Pandoc" / "pandoc.exe",
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return None


def install_hint() -> str:
    return (
        "Pandoc не найден в PATH. Установи одним из способов:\n"
        "  • Debian/Ubuntu: sudo apt-get install -y pandoc\n"
        "  • macOS (brew):  brew install pandoc\n"
        "  • Windows: winget install --id JohnMacFarlane.Pandoc\n"
        "  • Бинарник без root: "
        "https://github.com/jgm/pandoc/releases/latest"
    )