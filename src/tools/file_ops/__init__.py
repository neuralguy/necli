"""Native file operations — no shell, no heredoc.

DOCX is handled exclusively by the native :mod:`docx_engine` through the
single semantic ``docx`` tool; the legacy Pandoc/HTML/screenshot stack is gone.
"""

from tools.file_ops.docx_tool import docx
from tools.file_ops.grep import execute_grep
from tools.file_ops.patch import patch_file
from tools.file_ops.pptx_tool import pptx
from tools.file_ops.read import read
from tools.file_ops.write import create_file

__all__ = [
    "create_file",
    "docx",
    "execute_grep",
    "patch_file",
    "pptx",
    "read",
]
