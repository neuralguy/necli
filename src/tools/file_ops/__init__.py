"""Native file operations — no shell, no heredoc.

Подпакет разбит по операциям:
- read.py — read_files + helpers (_read_single_file, _apply_lines_filter)
- write.py — create_file (create-or-overwrite)
- patch.py — patch_file
- _fuzzy.py — fuzzy find/replace для patch_file

Все публичные имена реэкспортированы для обратной совместимости
с `from tools.file_ops import ...`.
"""

from tools.file_ops.docx_screenshot import docx_screenshot
from tools.file_ops.docx_writer import create_docx
from tools.file_ops.grep import execute_grep
from tools.file_ops.patch import patch_file
from tools.file_ops.read import MAX_READ_FILES, read_files
from tools.file_ops.write import create_file

__all__ = [
    "MAX_READ_FILES",
    "create_docx",
    "create_file",
    "docx_screenshot",
    "execute_grep",
    "patch_file",
    "read_files",
]
