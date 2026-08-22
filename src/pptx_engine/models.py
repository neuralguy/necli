"""Core, JSON-serialisable presentation model.

The model mirrors the useful parts of the source engine: each top-level OOXML
object retains its original XML slice and XML range.  Unmodified data can then
be written without regeneration, while modified objects are rebuilt surgically.
All geometry is in EMU (914400 EMU per inch) and rotations use OOXML units
(1/60000 of a degree).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ElementType = Literal[
    "text", "shape", "picture", "group", "table", "chart", "passthrough"
]
Fill = dict[str, Any]


@dataclass(slots=True)
class EmuRect:
    x: int = 0
    y: int = 0
    cx: int = 0
    cy: int = 0


@dataclass(slots=True)
class Transform:
    offset: EmuRect = field(default_factory=EmuRect)
    rot: int = 0
    flip_h: bool = False
    flip_v: bool = False


@dataclass(slots=True)
class ByteAnchor:
    sp_index: int
    original_xml: str
    range: tuple[int, int]
    gap_after: str = ""


@dataclass(slots=True)
class TextRun:
    text: str
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strike: bool | None = None
    font_size: float | None = None
    font_family: str | None = None
    color: str | None = None
    hyperlink: str | None = None
    baseline: float | None = None
    letter_spacing: float | None = None
    field: str | None = None


@dataclass(slots=True)
class Paragraph:
    runs: list[TextRun] = field(default_factory=list)
    align: str | None = None
    level: int | None = None
    line_height: float | None = None
    line_exact: float | None = None
    space_before: float | None = None
    space_after: float | None = None
    mar_l: int | None = None
    indent: int | None = None
    bullet: dict[str, Any] | None = None


@dataclass(slots=True)
class TextBody:
    paragraphs: list[Paragraph] = field(default_factory=list)
    anchor: Literal["top", "middle", "bottom"] | None = None
    insets: dict[str, int] | None = None
    autofit: str | None = None
    wrap: bool | None = None
    font_scale: float | None = None


@dataclass(slots=True)
class Element:
    id: str
    type: ElementType
    anchor: ByteAnchor
    transform: Transform = field(default_factory=Transform)
    name: str | None = None
    descr: str | None = None
    placeholder: str | None = None
    nv_id: str | None = None
    preset_geometry: str | None = None
    adjust: dict[str, int] = field(default_factory=dict)
    fill: Fill | None = None
    stroke: dict[str, Any] | None = None
    shadow: dict[str, Any] | None = None
    glow: dict[str, Any] | None = None
    text: TextBody | None = None
    media_ref: str | None = None
    media_r_id: str | None = None
    src_rect: dict[str, float] | None = None
    opacity: float | None = None
    children: list[Element] = field(default_factory=list)
    child_offset: EmuRect | None = None
    col_widths: list[int] = field(default_factory=list)
    row_heights: list[int] = field(default_factory=list)
    rows: list[list[dict[str, Any]]] = field(default_factory=list)
    chart: dict[str, Any] | None = None
    kind: str | None = None
    connection: dict[str, Any] | None = None
    dirty: bool = False
    dirty_transform: bool = False
    dirty_fill: bool = False
    dirty_stroke: bool = False
    dirty_src_rect: bool = False
    dirty_ppr: dict[str, Any] | None = None

    def mark_clean(self) -> None:
        self.dirty = self.dirty_transform = self.dirty_fill = False
        self.dirty_stroke = self.dirty_src_rect = False
        self.dirty_ppr = None
        for child in self.children:
            child.mark_clean()

    @property
    def is_dirty(self) -> bool:
        return any(
            (
                self.dirty,
                self.dirty_transform,
                self.dirty_fill,
                self.dirty_stroke,
                self.dirty_src_rect,
                self.dirty_ppr,
            )
        ) or any(c.is_dirty for c in self.children)


@dataclass(slots=True)
class Slide:
    path: str
    original_xml: str
    body_prefix: str
    body_suffix: str
    elements: list[Element] = field(default_factory=list)
    layout_path: str | None = None
    master_path: str | None = None
    background: Fill | None = None
    decorations: list[Element] = field(default_factory=list)
    structure_dirty: bool = False

    @property
    def is_dirty(self) -> bool:
        return self.structure_dirty or any(e.is_dirty for e in self.elements)


@dataclass(slots=True)
class SlideSize:
    cx: int = 12_192_000
    cy: int = 6_858_000


@dataclass(slots=True)
class Deck:
    slides: list[Slide]
    size: SlideSize
    original_hash: str
    theme: dict[str, str] = field(default_factory=dict)


def to_primitive(value: Any) -> Any:
    """Convert model objects recursively to a stable JSON-compatible value."""
    if hasattr(value, "__dataclass_fields__"):
        return {k: to_primitive(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): to_primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(v) for v in value]
    return value


def text_from_body(body: TextBody | None) -> str:
    if not body:
        return ""
    return "\n".join("".join(run.text for run in para.runs) for para in body.paragraphs)


def element_by_id(
    slide: Slide, element_id: str, recursive: bool = True
) -> Element | None:
    for element in slide.elements:
        if element.id == element_id:
            return element
        if recursive:
            found = element_by_id_in(element.children, element_id)
            if found:
                return found
    return None


def element_by_id_in(elements: list[Element], element_id: str) -> Element | None:
    for element in elements:
        if element.id == element_id:
            return element
        found = element_by_id_in(element.children, element_id)
        if found:
            return found
    return None
