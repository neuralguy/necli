"""Модели данных."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RevisionInfo:
    author: str
    date: str | None = None
    id: str | None = None


@dataclass
class Run:
    text: str
    raw_r_pr: str | None = None
    style_id: str | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    color: str | None = None
    size_half_points: int | None = None
    font: str | None = None
    char_spacing_twips: int | None = None
    char_scale_pct: int | None = None
    highlight: str | None = None
    vert_align: str | None = None
    em: str | None = None
    link: dict | None = None  # {href, rId?, tooltip?}
    comment_ids: list[str] | None = None
    ins: RevisionInfo | None = None
    del_: RevisionInfo | None = None
    note_ref: dict | None = None  # {kind,id}
    xe_term: str | None = None
    ref_field: str | None = None
    instr_field: str | None = None
    r_pr_change: dict | None = None
    math: dict | None = None  # {omml}
    ruby: dict | None = None  # {rt, xml}


@dataclass
class ParaFormat:
    align: str | None = None
    line_spacing: float | None = None
    line_rule: str | None = None
    line_raw_twips: int | None = None
    indent_left: int | None = None
    indent_right: int | None = None
    indent_first_line: int | None = None
    space_before: int | None = None
    space_after: int | None = None
    page_break_before: bool = False
    keep_next: bool = False
    keep_lines: bool = False
    widow_control: bool | None = None
    contextual_spacing: bool = False
    shading_fill: str | None = None
    borders: str | None = None
    tab_stops: list[dict] | None = None
    drop_cap: dict | None = None
    bidi: bool = False


@dataclass
class CommentInfo:
    id: str
    author: str
    text: str
    initials: str | None = None
    date: str | None = None
    parent_id: str | None = None
    done: bool = False
    para_id: str | None = None


@dataclass
class NoteInfo:
    id: str
    text: str
    rich_paras: list | None = None


@dataclass
class SourceInfo:
    tag: str
    type: str
    author: str
    title: str
    year: str
    publisher: str | None = None
    url: str | None = None


@dataclass
class GeneratedBlock:
    type: str  # paragraph|heading|listItem
    runs: list[Run] = field(default_factory=list)
    level: int | None = None
    style_id: str | None = None
    list: dict | None = None  # {kind,numId,ilvl}
    format: ParaFormat | None = None
    raw_p_pr: str | None = None
    bookmarks: list[str] | None = None
    hidden_bookmarks: list[str] | None = None
    comment_starts: list[str] | None = None
    comment_ends: list[str] | None = None
    sdt_shell: dict | None = None
    p_pr_change: str | None = None


@dataclass
class Block:
    id: str
    type: str
    docx_index: int | None
    original_xml: str | None = None
    level: int | None = None
    style_id: str | None = None
    list: dict | None = None
    format: ParaFormat | None = None
    raw_p_pr: str | None = None
    runs: list[Run] | None = None
    label: str | None = None
    preview_text: str | None = None
    image_data_url: str | None = None
    image_width_px: int | None = None
    image_height_px: int | None = None
    image_align: str | None = None
    image_wrap: str | None = None
    table: Any | None = None
    field_display: dict | None = None
    hidden: bool = False
    decorative: bool = False
    sdt_shell: dict | None = None
    bookmarks: list[str] | None = None
    hidden_bookmarks: list[str] | None = None
    comment_starts: list[str] | None = None
    comment_ends: list[str] | None = None
    move_revision: str | None = None
    p_pr_change_info: dict | None = None
    block_revision: dict | None = None
    chart_display: dict | None = None
    formula_display: dict | None = None


@dataclass
class ParsedDoc:
    blocks: list[Block]
    comments: list[CommentInfo]
    footnotes: list[NoteInfo]
    endnotes: list[NoteInfo]
    sources: list[SourceInfo]
    inks: list[dict]
    protection: dict | None
    styles: dict[str, dict]
    doc_defaults: dict | None
    heading_style_ids: dict[int, str]
    list_paragraph_style_id: str | None
    numbering: dict[str, dict]
    theme_fonts: dict | None
    theme_colors: dict | None
    watermark_text: str | None
    header_text: str | None
    footer_text: str | None
    footer_has_page_number: bool
    title_pg: bool
    even_and_odd_headers: bool
    internal: dict
    extras: dict


TOTAL_PAGES_MARK = "\ue000"
