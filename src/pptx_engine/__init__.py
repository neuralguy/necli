"""Pure-Python PPTX engine, renderer and agent CLI."""

from .archive import PackageArchive, Relationship, rels_path_for, resolve_target
from .engine import PptxDocument, PptxError, create_blank_pptx, element_xml, open_pptx
from .models import (
    Deck,
    Element,
    EmuRect,
    Paragraph,
    Slide,
    SlideSize,
    TextBody,
    TextRun,
    Transform,
    to_primitive,
)
from .operations import apply_operation, apply_operations
from .parser import parse_deck, parse_slide, parse_theme
from .render import (
    build_render_slide,
    emu_to_px,
    make_viewport,
    pt_to_px,
    render_png,
    render_svg,
    rot_to_deg,
)

__all__ = [
    "Deck",
    "Element",
    "EmuRect",
    "PackageArchive",
    "Paragraph",
    "PptxDocument",
    "PptxError",
    "Relationship",
    "Slide",
    "SlideSize",
    "TextBody",
    "TextRun",
    "Transform",
    "apply_operation",
    "apply_operations",
    "build_render_slide",
    "create_blank_pptx",
    "element_xml",
    "emu_to_px",
    "make_viewport",
    "open_pptx",
    "parse_deck",
    "parse_slide",
    "parse_theme",
    "pt_to_px",
    "rels_path_for",
    "render_png",
    "render_svg",
    "resolve_target",
    "rot_to_deg",
    "to_primitive",
]
