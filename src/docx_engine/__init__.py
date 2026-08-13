"""docx_engine — движок чтения и хирургического редактирования .docx (OOXML)."""

# Backward-compatible import alias without a physical ``types.py`` file.  A
# file with that name shadows Python's stdlib ``types`` module when the package
# directory itself is on sys.path/cwd, which can break imports as early as
# ``dataclasses``.
import sys as _sys

from . import models as types
from .models import (
    TOTAL_PAGES_MARK,
    Block,
    CommentInfo,
    GeneratedBlock,
    NoteInfo,
    ParaFormat,
    ParsedDoc,
    RevisionInfo,
    Run,
    SourceInfo,
)

_sys.modules.setdefault(__name__ + ".types", types)
from .blank import (
    BLANK_BULLET_NUM_ID,
    BLANK_NUMBERING_XML,
    BLANK_ORDERED_NUM_ID,
    CustomNumberingLevel,
    abstract_num_xml,
    build_blank_docx,
)
from .chart import (
    CHART_WORKBOOK_REL_TYPE,
    build_chart_part_xml,
    build_chart_workbook_xlsx,
    parse_chart_part_xml,
    patch_chart_part_xml,
    patch_chart_workbook_xlsx,
)
from .generate import (
    TABLE_HEADER_FILL,
    WORDART_PRESETS,
    apply_image_wrap,
    build_textbox_paragraph_xml,
    build_word_art_paragraph_xml,
    generate_caption_xml,
    generate_index_field_xml,
    generate_paragraph_xml,
    generate_table_model_xml,
    generate_table_xml,
    generate_toc_field_xml,
    inline_runs_xml,
    merge_p_pr_format,
    patch_field_paragraph_xml,
    patch_image_paragraph_xml,
    patch_math_tokens,
    patch_table_cell_texts,
    strip_p_pr_change,
)
from .ink import (
    INK_NAME_PREFIX,
    anchored_ink_run_xml,
    find_ink_runs,
    inject_ink_runs_into_paragraph,
    strip_ink_runs,
)
from .mathml import (
    latex_to_omml,
    math_paragraph_xml,
    math_tokens_of,
    omml_fragments_of,
    omml_to_mathml,
)
from .notes import next_note_id, parse_notes_xml
from .parse import parse_docx
from .patch import find_chart_workbook_path, read_docx_part_base64, save_docx
from .protection import hash_protection_password, verify_protection_password
from .scan import scan_body
from .section import (
    DEFAULT_SECTION,
    apply_page_num_type,
    apply_section_settings,
    apply_section_start_type,
    read_page_color,
    read_section_settings,
    read_sections,
    section_settings_from_xml,
)
from .sources import bibliography_line, citation_text, parse_sources_xml
from .symbol_fonts import decode_symbol_char, decode_symbol_text, is_symbol_font
from .theme import read_theme_colors, read_theme_fonts
from .watermark import read_watermark_text

__all__ = [
    "BLANK_BULLET_NUM_ID",
    "BLANK_NUMBERING_XML",
    "BLANK_ORDERED_NUM_ID",
    "CHART_WORKBOOK_REL_TYPE",
    "DEFAULT_SECTION",
    "INK_NAME_PREFIX",
    "TABLE_HEADER_FILL",
    "TOTAL_PAGES_MARK",
    "WORDART_PRESETS",
    "Block",
    "CommentInfo",
    "CustomNumberingLevel",
    "GeneratedBlock",
    "NoteInfo",
    "ParaFormat",
    "ParsedDoc",
    "RevisionInfo",
    "Run",
    "SourceInfo",
    "abstract_num_xml",
    "anchored_ink_run_xml",
    "apply_image_wrap",
    "apply_page_num_type",
    "apply_section_settings",
    "apply_section_start_type",
    "bibliography_line",
    "build_blank_docx",
    "build_chart_part_xml",
    "build_chart_workbook_xlsx",
    "build_textbox_paragraph_xml",
    "build_word_art_paragraph_xml",
    "citation_text",
    "decode_symbol_char",
    "decode_symbol_text",
    "find_chart_workbook_path",
    "find_ink_runs",
    "generate_caption_xml",
    "generate_index_field_xml",
    "generate_paragraph_xml",
    "generate_table_model_xml",
    "generate_table_xml",
    "generate_toc_field_xml",
    "hash_protection_password",
    "inject_ink_runs_into_paragraph",
    "inline_runs_xml",
    "is_symbol_font",
    "latex_to_omml",
    "math_paragraph_xml",
    "math_tokens_of",
    "merge_p_pr_format",
    "next_note_id",
    "omml_fragments_of",
    "omml_to_mathml",
    "parse_chart_part_xml",
    "parse_docx",
    "parse_notes_xml",
    "parse_sources_xml",
    "patch_chart_part_xml",
    "patch_chart_workbook_xlsx",
    "patch_field_paragraph_xml",
    "patch_image_paragraph_xml",
    "patch_math_tokens",
    "patch_table_cell_texts",
    "read_docx_part_base64",
    "read_page_color",
    "read_section_settings",
    "read_sections",
    "read_theme_colors",
    "read_theme_fonts",
    "read_watermark_text",
    "save_docx",
    "scan_body",
    "section_settings_from_xml",
    "strip_ink_runs",
    "strip_p_pr_change",
    "types",
    "verify_protection_password",
]
