"""Native file operations — no shell, no heredoc.

Подпакет разбит по операциям:
- read.py — read_files + helpers (_read_single_file, _apply_lines_filter)
- write.py — write_file, create_file
- patch.py — patch_file
- manage.py — delete_file, rename_file, copy_file, move_file
- _fuzzy.py — fuzzy find/replace для patch_file

Все публичные имена реэкспортированы для обратной совместимости
с `from tools.file_ops import ...`.
"""

from tools.file_ops.read import read_files, MAX_READ_FILES
from tools.file_ops.write import write_file, create_file
from tools.file_ops.patch import patch_file
from tools.file_ops.manage import delete_file, rename_file, copy_file, move_file
from tools.file_ops.docx_writer import create_docx
from tools.file_ops.docx_screenshot import docx_screenshot
from tools.file_ops.diff_apply import apply_diff

__all__ = [
    "read_files",
    "write_file",
    "create_file",
    "patch_file",
    "delete_file",
    "rename_file",
    "copy_file",
    "move_file",
    "create_docx",
    "docx_screenshot",
    "apply_diff",
    "MAX_READ_FILES",
]