"""Pure-Python PPTX editing engine.

This module provides the durable behaviour shared by the original engine:
parsing keeps untouched OOXML, while edits regenerate only the relevant slide
objects.  It intentionally has no JavaScript, Node.js, Office automation or
external binary dependency.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

from .archive import PackageArchive, Relationship, relative_target, rels_path_for, resolve_target
from .models import (
    ByteAnchor,
    Deck,
    Element,
    EmuRect,
    Paragraph,
    Slide,
    SlideSize,
    TextBody,
    TextRun,
    Transform,
    element_by_id,
    to_primitive,
)
from .parser import parse_deck, parse_slide
from .xmlutil import NS, xml_escape_attr, xml_escape_text

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
P_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = NS["p"]
A_NS = NS["a"]
R_NS = NS["r"]


class PptxError(RuntimeError):
    """Raised for explicit, agent-actionable presentation errors."""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _new_anchor(index: int, xml: str = "") -> ByteAnchor:
    return ByteAnchor(index, xml, (0, len(xml)))


def _xfrm_xml(transform: Transform, group: bool = False) -> str:
    attrs = "".join(
        (
            f' rot="{transform.rot}"' if transform.rot else "",
            ' flipH="1"' if transform.flip_h else "",
            ' flipV="1"' if transform.flip_v else "",
        )
    )
    rect = transform.offset
    child = '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>' if group else ""
    return f'<a:xfrm{attrs}><a:off x="{rect.x}" y="{rect.y}"/><a:ext cx="{rect.cx}" cy="{rect.cy}"/>{child}</a:xfrm>'


def _colour_xml(color: str | None) -> str:
    value = (color or "#000000").lstrip("#")
    rgb = value[:6] if len(value) >= 6 else "000000"
    alpha = value[6:8] if len(value) >= 8 else None
    return (
        f'<a:srgbClr val="{xml_escape_attr(rgb.upper())}"/>'
        if not alpha
        else f'<a:srgbClr val="{xml_escape_attr(rgb.upper())}"><a:alpha val="{round(int(alpha, 16) * 100000 / 255)}"/></a:srgbClr>'
    )


def _fill_xml(fill: dict[str, Any] | None) -> str:
    if not fill or fill.get("type") == "none":
        return "<a:noFill/>"
    if fill.get("type") == "solid":
        return f"<a:solidFill>{_colour_xml(fill.get('color'))}</a:solidFill>"
    if fill.get("type") == "gradient":
        stops = "".join(
            f'<a:gs pos="{round(float(stop.get("pos", 0)) * 100000)}">{_colour_xml(stop.get("color"))}</a:gs>'
            for stop in fill.get("stops", [])
        )
        angle = round(float(fill.get("angle", 0)) * 60000)
        path = fill.get("path")
        extra = (
            f'<a:path path="{xml_escape_attr(str(path))}"/>'
            if path
            else f'<a:lin ang="{angle}" scaled="1"/>'
        )
        return f'<a:gradFill rotWithShape="1"><a:gsLst>{stops}</a:gsLst>{extra}</a:gradFill>'
    if fill.get("type") == "pattern":
        return f'<a:pattFill prst="{xml_escape_attr(fill.get("preset", "pct5"))}"><a:fgClr>{_colour_xml(fill.get("fg"))}</a:fgClr><a:bgClr>{_colour_xml(fill.get("bg"))}</a:bgClr></a:pattFill>'
    return "<a:noFill/>"


def _stroke_xml(stroke: dict[str, Any] | None) -> str:
    if not stroke:
        return "<a:ln><a:noFill/></a:ln>"
    width = _as_int(stroke.get("width"), 12700)
    cap = f' cap="{xml_escape_attr(str(stroke["cap"]))}"' if stroke.get("cap") else ""
    dash = (
        f'<a:prstDash val="{xml_escape_attr(str(stroke["dash"]))}"/>' if stroke.get("dash") else ""
    )
    ends = ""
    for source, tag in (("head_end", "headEnd"), ("tail_end", "tailEnd")):
        if stroke.get(source):
            end = stroke[source]
            ends += (
                f'<a:{tag} type="{xml_escape_attr(str(end.get("type", "none")))}"'
                + (f' w="{xml_escape_attr(str(end["w"]))}"' if end.get("w") else "")
                + (f' len="{xml_escape_attr(str(end["len"]))}"' if end.get("len") else "")
                + "/>"
            )
    return f'<a:ln w="{width}"{cap}>{_fill_xml(stroke.get("fill"))}{dash}{ends}</a:ln>'


def _run_xml(run: TextRun) -> str:
    attrs = ""
    attrs += ' b="1"' if run.bold else ""
    attrs += ' i="1"' if run.italic else ""
    attrs += ' u="sng"' if run.underline else ""
    attrs += ' strike="sngStrike"' if run.strike else ""
    attrs += f' sz="{round(run.font_size * 100)}"' if run.font_size is not None else ""
    attrs += f' baseline="{round(run.baseline * 1000)}"' if run.baseline is not None else ""
    attrs += f' spc="{round(run.letter_spacing * 100)}"' if run.letter_spacing is not None else ""
    props = ""
    if run.font_family:
        props += f'<a:latin typeface="{xml_escape_attr(run.font_family)}"/>'
    if run.color:
        props += f"<a:solidFill>{_colour_xml(run.color)}</a:solidFill>"
    if run.hyperlink:
        # external/internal relationships are created by add_hyperlink when a document owns the run;
        # textual JSON operations preserve a supplied relationship identifier directly.
        props += f'<a:hlinkClick r:id="{xml_escape_attr(run.hyperlink)}"/>'
    rpr = f"<a:rPr{attrs}>{props}</a:rPr>" if (attrs or props) else "<a:rPr/>"
    if run.field:
        return f'<a:fld type="{xml_escape_attr(run.field)}" id="{{00000000-0000-0000-0000-000000000000}}">{rpr}<a:t>{xml_escape_text(run.text)}</a:t></a:fld>'
    return f"<a:r>{rpr}<a:t>{xml_escape_text(run.text)}</a:t></a:r>"


def _paragraph_xml(paragraph: Paragraph) -> str:
    attrs = ""
    align_map = {"left": "l", "center": "ctr", "right": "r", "justify": "just"}
    if paragraph.align:
        attrs += f' algn="{align_map.get(paragraph.align, paragraph.align)}"'
    if paragraph.level is not None:
        attrs += f' lvl="{paragraph.level}"'
    if paragraph.mar_l is not None:
        attrs += f' marL="{paragraph.mar_l}"'
    if paragraph.indent is not None:
        attrs += f' indent="{paragraph.indent}"'
    extras = ""
    if paragraph.line_height is not None:
        extras += f'<a:lnSpc><a:spcPct val="{round(paragraph.line_height * 1000)}"/></a:lnSpc>'
    elif paragraph.line_exact is not None:
        extras += f'<a:lnSpc><a:spcPts val="{round(paragraph.line_exact * 100)}"/></a:lnSpc>'
    if paragraph.space_before is not None:
        extras += f'<a:spcBef><a:spcPts val="{round(paragraph.space_before * 100)}"/></a:spcBef>'
    if paragraph.space_after is not None:
        extras += f'<a:spcAft><a:spcPts val="{round(paragraph.space_after * 100)}"/></a:spcAft>'
    if paragraph.bullet:
        if paragraph.bullet.get("type") == "none":
            extras += "<a:buNone/>"
        elif paragraph.bullet.get("type") == "number":
            extras += f'<a:buAutoNum type="{xml_escape_attr(paragraph.bullet.get("num_type", "arabicPeriod"))}"/>'
        else:
            extras += f'<a:buChar char="{xml_escape_attr(paragraph.bullet.get("char", "•"))}"/>'
    ppr = f"<a:pPr{attrs}>{extras}</a:pPr>" if (attrs or extras) else ""
    return f"<a:p>{ppr}{''.join(_run_xml(run) for run in paragraph.runs)}<a:endParaRPr/></a:p>"


def _text_xml(body: TextBody | None) -> str:
    if body is None:
        return ""
    insets = body.insets or {"l": 91440, "t": 45720, "r": 91440, "b": 45720}
    anchor = {"top": "t", "middle": "ctr", "bottom": "b"}.get(body.anchor or "top", "t")
    wrap = "none" if body.wrap is False else "square"
    body_pr = f'<a:bodyPr lIns="{insets.get("l", 91440)}" tIns="{insets.get("t", 45720)}" rIns="{insets.get("r", 91440)}" bIns="{insets.get("b", 45720)}" anchor="{anchor}" wrap="{wrap}">'
    if body.autofit == "shrink":
        body_pr += f'<a:normAutofit fontScale="{round((body.font_scale or 1) * 100000)}"/>'
    elif body.autofit == "resize":
        body_pr += "<a:spAutoFit/>"
    else:
        body_pr += "<a:noAutofit/>"
    body_pr += "</a:bodyPr>"
    paragraphs = body.paragraphs or [Paragraph([TextRun("")])]
    return f"<p:txBody>{body_pr}<a:lstStyle/>{''.join(_paragraph_xml(p) for p in paragraphs)}</p:txBody>"


def element_xml(element: Element) -> str:
    """Generate valid XML for a changed element. Clean elements use original XML."""
    if not element.is_dirty and element.anchor.original_xml:
        return element.anchor.original_xml
    name = xml_escape_attr(element.name or element.type.title())
    descr = f' descr="{xml_escape_attr(element.descr)}"' if element.descr else ""
    numeric_id = _as_int(element.nv_id or element.id, 1)
    if element.type in {"shape", "text"}:
        geometry = element.preset_geometry or "rect"
        adj = "".join(
            f'<a:gd name="{xml_escape_attr(k)}" fmla="val {v}"/>' for k, v in element.adjust.items()
        )
        geometry_xml = (
            f'<a:prstGeom prst="{xml_escape_attr(geometry)}"><a:avLst>{adj}</a:avLst></a:prstGeom>'
        )
        sp_pr = f"<p:spPr>{_xfrm_xml(element.transform)}{geometry_xml}{_fill_xml(element.fill)}{_stroke_xml(element.stroke)}</p:spPr>"
        ph = f'<p:ph type="{xml_escape_attr(element.placeholder)}"/>' if element.placeholder else ""
        return f'<p:sp><p:nvSpPr><p:cNvPr id="{numeric_id}" name="{name}"{descr}/><p:cNvSpPr txBox="{1 if element.type == "text" else 0}"/><p:nvPr>{ph}</p:nvPr></p:nvSpPr>{sp_pr}{_text_xml(element.text)}</p:sp>'
    if element.type == "picture":
        embed = (
            f' r:embed="{xml_escape_attr(element.media_r_id or "")}"' if element.media_r_id else ""
        )
        src = ""
        if element.src_rect:
            src = (
                "<a:srcRect "
                + " ".join(f'{k}="{round(float(v) * 100000)}"' for k, v in element.src_rect.items())
                + "/>"
            )
        alpha = (
            f'<a:alphaModFix amt="{round(max(0, min(1, element.opacity)) * 100000)}"/>'
            if element.opacity is not None and element.opacity < 0.999
            else ""
        )
        geom = element.preset_geometry or "rect"
        return f'<p:pic><p:nvPicPr><p:cNvPr id="{numeric_id}" name="{name}"{descr}/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip{embed}>{alpha}</a:blip>{src}<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>{_xfrm_xml(element.transform)}<a:prstGeom prst="{xml_escape_attr(geom)}"><a:avLst/></a:prstGeom>{_fill_xml(element.fill)}{_stroke_xml(element.stroke)}</p:spPr></p:pic>'
    if element.type == "group":
        child_xml = "".join(element_xml(child) for child in element.children)
        return f'<p:grpSp><p:nvGrpSpPr><p:cNvPr id="{numeric_id}" name="{name}"{descr}/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr>{_xfrm_xml(element.transform, True)}</p:grpSpPr>{child_xml}</p:grpSp>'
    if element.type == "table":
        return _table_xml(element, numeric_id, name, descr)
    # Chart, SmartArt, OLE and unknown elements are always preserved when possible.
    if element.anchor.original_xml:
        return element.anchor.original_xml
    return ""


def _table_xml(element: Element, numeric_id: int, name: str, descr: str) -> str:
    widths = element.col_widths or [max(1, element.transform.offset.cx)]
    heights = element.row_heights or [max(1, element.transform.offset.cy)]
    grid = "".join(f'<a:gridCol w="{max(1, w)}"/>' for w in widths)
    table_rows = ""
    for row_index, height in enumerate(heights):
        cells = element.rows[row_index] if row_index < len(element.rows) else []
        cells_xml = ""
        for col_index in range(len(widths)):
            cell = cells[col_index] if col_index < len(cells) else {}
            text = cell.get("text") if isinstance(cell, dict) else None
            if isinstance(text, dict):
                # JSON operation inputs may pass text as {paragraphs: ...}; simple string remains accepted.
                text = TextBody([Paragraph([TextRun(str(text))])])
            if isinstance(text, str):
                text = TextBody([Paragraph([TextRun(text)])])
            attrs = ""
            if isinstance(cell, dict) and cell.get("grid_span", 1) > 1:
                attrs += f' gridSpan="{cell["grid_span"]}"'
            if isinstance(cell, dict) and cell.get("row_span", 1) > 1:
                attrs += f' rowSpan="{cell["row_span"]}"'
            if isinstance(cell, dict) and cell.get("merged"):
                attrs += ' hMerge="1"'
            fill = (
                _fill_xml(cell.get("fill")) if isinstance(cell, dict) and cell.get("fill") else ""
            )
            body = _text_xml(
                text if isinstance(text, TextBody) else TextBody([Paragraph([TextRun("")])])
            )
            # Tables use DrawingML's a:txBody (not PresentationML p:txBody).
            body = body.replace("<p:txBody>", "<a:txBody>").replace("</p:txBody>", "</a:txBody>")
            cells_xml += f"<a:tc{attrs}>{body}<a:tcPr>{fill}</a:tcPr></a:tc>"
        table_rows += f'<a:tr h="{max(1, height)}">{cells_xml}</a:tr>'
    xfrm = element.transform.offset
    frame_xfrm = (
        f'<p:xfrm><a:off x="{xfrm.x}" y="{xfrm.y}"/><a:ext cx="{xfrm.cx}" cy="{xfrm.cy}"/></p:xfrm>'
    )
    return f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{numeric_id}" name="{name}"{descr}/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>{frame_xfrm}<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>{table_rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'


class PptxDocument:
    """An editable presentation built on :class:`PackageArchive`."""

    def __init__(self, archive: PackageArchive, deck: Deck):
        self.archive = archive
        self.deck = deck

    @classmethod
    def open_bytes(cls, data: bytes) -> PptxDocument:
        archive = PackageArchive.open(data)
        return cls(archive, parse_deck(archive))

    @classmethod
    def open_file(cls, file_path: str) -> PptxDocument:
        return cls.open_bytes(Path(file_path).read_bytes())

    @classmethod
    def create_blank(cls, width_emu: int = 12_192_000, height_emu: int = 6_858_000) -> PptxDocument:
        archive = PackageArchive(_blank_entries(width_emu, height_emu), "")
        document = cls(archive, parse_deck(archive))
        document.deck.original_hash = sha256(document.to_bytes()).hexdigest()
        document.archive.original_hash = document.deck.original_hash
        return document

    def to_model(self) -> dict[str, Any]:
        return to_primitive(self.deck)

    def _refresh_slide(self, index: int) -> Slide:
        slide = self.deck.slides[index]
        relations = {
            r.id: (
                r.target if r.target_mode == "External" else resolve_target(slide.path, r.target)
            )
            for r in self.archive.read_rels(slide.path).values()
        }
        chain = self.archive.resolve_slide_chain(slide.path)
        fresh = parse_slide(
            slide.original_xml,
            slide.path,
            self.deck.theme,
            relations,
            chain.get("layout_path"),
            chain.get("master_path"),
        )
        self.deck.slides[index] = fresh
        return fresh

    def slide(self, index: int) -> Slide:
        if not 0 <= index < len(self.deck.slides):
            raise PptxError(f"slide_index {index} is outside [0, {len(self.deck.slides) - 1}]")
        return self.deck.slides[index]

    def _next_nv_id(self, slide: Slide) -> int:
        max_id = 1

        def visit(elements: Iterable[Element]) -> None:
            nonlocal max_id
            for e in elements:
                max_id = max(max_id, _as_int(e.nv_id or e.id, 1))
                visit(e.children)

        visit(slide.elements)
        return max_id + 1

    def _new_element(
        self, slide: Slide, kind: str, transform: dict[str, Any] | None = None, **kwargs: Any
    ) -> Element:
        number = self._next_nv_id(slide)
        rect_data = (transform or {}).get("offset", transform or {})
        rect = EmuRect(
            _as_int(rect_data.get("x")),
            _as_int(rect_data.get("y")),
            _as_int(rect_data.get("cx")),
            _as_int(rect_data.get("cy")),
        )
        xfrm = Transform(
            rect,
            _as_int((transform or {}).get("rot")),
            bool((transform or {}).get("flip_h") or (transform or {}).get("flipH")),
            bool((transform or {}).get("flip_v") or (transform or {}).get("flipV")),
        )
        return Element(
            str(number),
            kind,
            _new_anchor(len(slide.elements)),
            xfrm,
            nv_id=str(number),
            name=kwargs.pop("name", f"{kind.title()} {number}"),
            dirty=True,
            **kwargs,
        )

    def add_shape(
        self,
        slide_index: int,
        *,
        shape: str = "rect",
        transform: dict[str, Any] | None = None,
        fill: dict[str, Any] | None = None,
        stroke: dict[str, Any] | None = None,
        text: str | None = None,
        name: str | None = None,
    ) -> Element:
        slide = self.slide(slide_index)
        body = TextBody([Paragraph([TextRun(text)])]) if text is not None else None
        element = self._new_element(
            slide,
            "shape",
            transform,
            preset_geometry=shape,
            fill=fill or {"type": "solid", "color": "#FFFFFF"},
            stroke=stroke,
            text=body,
            name=name,
        )
        slide.elements.append(element)
        slide.structure_dirty = True
        return element

    def add_text(
        self,
        slide_index: int,
        *,
        text: str,
        transform: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> Element:
        slide = self.slide(slide_index)
        style = style or {}
        run = TextRun(
            text,
            bold=style.get("bold"),
            italic=style.get("italic"),
            font_size=style.get("font_size"),
            font_family=style.get("font_family"),
            color=style.get("color"),
        )
        element = self._new_element(
            slide,
            "text",
            transform,
            fill={"type": "none"},
            stroke=None,
            text=TextBody([Paragraph([run], align=style.get("align"))]),
            name=name,
        )
        slide.elements.append(element)
        slide.structure_dirty = True
        return element

    def add_picture(
        self,
        slide_index: int,
        image: bytes,
        extension: str,
        transform: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> Element:
        slide = self.slide(slide_index)
        ext = extension.lower().lstrip(".") or "png"
        media_path, media_r_id = self._add_media(slide, image, ext)
        element = self._new_element(
            slide,
            "picture",
            transform,
            media_ref=media_path,
            media_r_id=media_r_id,
            fill={"type": "none"},
            name=name,
        )
        slide.elements.append(element)
        slide.structure_dirty = True
        return element

    def add_table(
        self,
        slide_index: int,
        rows: list[list[Any]],
        transform: dict[str, Any] | None = None,
        col_widths: list[int] | None = None,
        row_heights: list[int] | None = None,
        name: str | None = None,
    ) -> Element:
        slide = self.slide(slide_index)
        column_count = max((len(r) for r in rows), default=1)
        rect = (transform or {}).get("offset", transform or {})
        total_width = _as_int(rect.get("cx"), 5_000_000)
        total_height = _as_int(rect.get("cy"), 2_000_000)
        column_widths = col_widths or [total_width // column_count] * column_count
        heights = row_heights or [total_height // max(1, len(rows))] * max(1, len(rows))
        parsed_rows: list[list[dict[str, Any]]] = []
        for row in rows:
            parsed_rows.append(
                [{"text": TextBody([Paragraph([TextRun(str(value))])])} for value in row]
            )
        element = self._new_element(
            slide,
            "table",
            transform,
            col_widths=column_widths,
            row_heights=heights,
            rows=parsed_rows,
            name=name,
        )
        slide.elements.append(element)
        slide.structure_dirty = True
        return element

    def set_text(
        self, slide_index: int, element_id: str, text: str, replace_all: bool = True
    ) -> Element:
        element = self.require_element(slide_index, element_id)
        if not element.text:
            if element.type not in {"shape", "text"}:
                raise PptxError(f"element {element_id} does not carry editable text")
            # Regular PowerPoint shapes can own a text body even when they were
            # initially created without one. Materialize it on first set_text.
            element.text = TextBody([Paragraph([TextRun("")])])
        if replace_all:
            element.text.paragraphs = [Paragraph([TextRun(text)])]
        else:
            first = (
                element.text.paragraphs[0].runs[0]
                if element.text.paragraphs and element.text.paragraphs[0].runs
                else None
            )
            if first:
                first.text = text
            else:
                element.text.paragraphs = [Paragraph([TextRun(text)])]
        element.dirty = True
        return element

    def replace_all(self, search: str, replacement: str, *, case_sensitive: bool = True) -> int:
        if not search:
            raise PptxError("search must not be empty")
        flags = 0 if case_sensitive else re.IGNORECASE
        count = 0
        for slide in self.deck.slides:
            for element in _walk(slide.elements):
                if element.text:
                    for paragraph in element.text.paragraphs:
                        for run in paragraph.runs:
                            run.text, changed = re.subn(
                                re.escape(search), replacement, run.text, flags=flags
                            )
                            count += changed
                            if changed:
                                element.dirty = True
                for row in element.rows:
                    for cell in row:
                        body = cell.get("text") if isinstance(cell, dict) else None
                        if isinstance(body, TextBody):
                            for para in body.paragraphs:
                                for run in para.runs:
                                    run.text, changed = re.subn(
                                        re.escape(search), replacement, run.text, flags=flags
                                    )
                                    count += changed
                                    if changed:
                                        element.dirty = True
        return count

    def transform(self, slide_index: int, element_id: str, patch: dict[str, Any]) -> Element:
        element = self.require_element(slide_index, element_id)
        offset = patch.get("offset", patch)
        for key in ("x", "y", "cx", "cy"):
            if key in offset:
                setattr(element.transform.offset, key, _as_int(offset[key]))
        if "rot" in patch:
            element.transform.rot = _as_int(patch["rot"])
        if "flip_h" in patch or "flipH" in patch:
            element.transform.flip_h = bool(patch.get("flip_h", patch.get("flipH")))
        if "flip_v" in patch or "flipV" in patch:
            element.transform.flip_v = bool(patch.get("flip_v", patch.get("flipV")))
        element.dirty_transform = True
        return element

    def set_fill(self, slide_index: int, element_id: str, fill: dict[str, Any]) -> Element:
        element = self.require_element(slide_index, element_id)
        element.fill = fill
        element.dirty_fill = True
        return element

    def set_stroke(
        self, slide_index: int, element_id: str, stroke: dict[str, Any] | None
    ) -> Element:
        element = self.require_element(slide_index, element_id)
        element.stroke = stroke
        element.dirty_stroke = True
        return element

    def set_font(self, slide_index: int, element_id: str, patch: dict[str, Any]) -> Element:
        element = self.require_element(slide_index, element_id)
        if not element.text:
            raise PptxError(f"element {element_id} does not carry editable text")
        for paragraph in element.text.paragraphs:
            for run in paragraph.runs:
                for key, attribute in (
                    ("font_size", "font_size"),
                    ("font_family", "font_family"),
                    ("color", "color"),
                    ("bold", "bold"),
                    ("italic", "italic"),
                    ("underline", "underline"),
                    ("strike", "strike"),
                ):
                    if key in patch:
                        setattr(run, attribute, patch[key])
        element.dirty = True
        return element

    def set_paragraph_format(
        self,
        slide_index: int,
        element_id: str,
        patch: dict[str, Any],
        paragraph_indices: list[int] | None = None,
    ) -> Element:
        element = self.require_element(slide_index, element_id)
        if not element.text:
            raise PptxError(f"element {element_id} does not carry editable text")
        indexes = paragraph_indices or list(range(len(element.text.paragraphs)))
        aliases = {
            "marL": "mar_l",
            "lineHeight": "line_height",
            "lineExact": "line_exact",
            "spaceBefore": "space_before",
            "spaceAfter": "space_after",
        }
        for index in indexes:
            if 0 <= index < len(element.text.paragraphs):
                paragraph = element.text.paragraphs[index]
                for key, value in patch.items():
                    attr = aliases.get(key, key)
                    if hasattr(paragraph, attr):
                        setattr(paragraph, attr, value)
        element.dirty = True
        return element

    def edit_table_cell(
        self, slide_index: int, element_id: str, row: int, column: int, text: str
    ) -> Element:
        element = self.require_element(slide_index, element_id)
        if element.type != "table":
            raise PptxError(f"element {element_id} is not a table")
        while len(element.rows) <= row:
            element.rows.append([])
        while len(element.rows[row]) <= column:
            element.rows[row].append({"text": TextBody([Paragraph([TextRun("")])])})
        element.rows[row][column]["text"] = TextBody([Paragraph([TextRun(text)])])
        element.dirty = True
        return element

    def delete_element(self, slide_index: int, element_id: str) -> bool:
        slide = self.slide(slide_index)
        found = _remove(slide.elements, element_id)
        if found:
            slide.structure_dirty = True
        return found

    def group(self, slide_index: int, element_ids: list[str], name: str | None = None) -> Element:
        slide = self.slide(slide_index)
        members = [e for e in slide.elements if e.id in set(element_ids)]
        if len(members) < 2:
            raise PptxError("group requires at least two top-level elements")
        x0 = min(e.transform.offset.x for e in members)
        y0 = min(e.transform.offset.y for e in members)
        x1 = max(e.transform.offset.x + e.transform.offset.cx for e in members)
        y1 = max(e.transform.offset.y + e.transform.offset.cy for e in members)
        group = self._new_element(
            slide,
            "group",
            {"x": x0, "y": y0, "cx": x1 - x0, "cy": y1 - y0},
            children=members,
            name=name,
        )
        slide.elements = [e for e in slide.elements if e.id not in set(element_ids)] + [group]
        slide.structure_dirty = True
        return group

    def ungroup(self, slide_index: int, group_id: str) -> list[Element]:
        slide = self.slide(slide_index)
        for i, element in enumerate(slide.elements):
            if element.id == group_id and element.type == "group":
                slide.elements[i : i + 1] = element.children
                slide.structure_dirty = True
                return element.children
        raise PptxError(f"group {group_id} was not found")

    def set_slide_background(self, slide_index: int, color: str) -> None:
        slide = self.slide(slide_index)
        slide.background = {"type": "solid", "color": color}
        # body_prefix owns p:cSld and is safe to patch structurally.
        background = f"<p:bg><p:bgPr><a:solidFill>{_colour_xml(color)}</a:solidFill><a:effectLst/></p:bgPr></p:bg>"
        if "<p:bg>" in slide.body_prefix:
            slide.body_prefix = re.sub(
                r"<p:bg>.*?</p:bg>", background, slide.body_prefix, flags=re.S
            )
        else:
            c_sld_end = slide.body_prefix.find(">", slide.body_prefix.find("<p:cSld")) + 1
            slide.body_prefix = (
                slide.body_prefix[:c_sld_end] + background + slide.body_prefix[c_sld_end:]
            )
        slide.structure_dirty = True

    def set_slide_hidden(self, slide_index: int, hidden: bool) -> None:
        slide = self.slide(slide_index)
        xml = self.archive.read_text("ppt/presentation.xml") or ""
        rels = self.archive.read_rels("ppt/presentation.xml")
        relation = next(
            (
                r
                for r in rels.values()
                if resolve_target("ppt/presentation.xml", r.target) == slide.path
            ),
            None,
        )
        if not relation:
            raise PptxError("slide relationship is missing")
        pattern = rf'(<p:sldId\b(?=[^>]*r:id="{re.escape(relation.id)}")[^>]*?)(\s*/>)'

        def repl(match: re.Match[str]) -> str:
            tag = re.sub(r'\s+show="(?:0|1|true|false)"', "", match.group(1))
            return tag + (' show="0"' if hidden else "") + match.group(2)

        self.archive.write_text("ppt/presentation.xml", re.sub(pattern, repl, xml))

    def duplicate_slide(self, source_index: int, clear_text: bool = False) -> Slide:
        source = self.slide(source_index)
        new_path = self._next_slide_path()
        self.archive.write_text(new_path, self.slide_xml(source))
        # Copy the relation graph but omit notes relationship so notes never alias.
        source_rels = self.archive.read_rels(source.path)
        self.archive.write_rels(
            new_path, {k: v for k, v in source_rels.items() if not v.type.endswith("/notesSlide")}
        )
        result = self._register_slide(source_index, new_path)
        if clear_text:
            for element in _walk(result.elements):
                if element.text:
                    element.text.paragraphs = [Paragraph([TextRun("")])]
                    element.dirty = True
        return result

    def insert_blank_slide(self, insertion_index: int) -> Slide:
        slide_count = len(self.deck.slides)
        if not 0 <= insertion_index <= slide_count:
            raise PptxError(
                f"insert_blank_slide slide_index {insertion_index} is outside "
                f"insertion range [0, {slide_count}] (use {slide_count} to append)"
            )
        source_index = max(0, insertion_index - 1)
        source = self.slide(source_index)
        new_path = self._next_slide_path()
        self.archive.write_text(new_path, _blank_slide_xml())
        layout = next(
            (
                r
                for r in self.archive.read_rels(source.path).values()
                if r.type.endswith("/slideLayout")
            ),
            None,
        )
        rels = {"rId1": Relationship("rId1", layout.type, layout.target)} if layout else {}
        self.archive.write_rels(new_path, rels)
        result = self._register_slide(source_index, new_path)
        if insertion_index == 0:
            self.move_slide(1, 0)
        return result

    def delete_slide(self, index: int) -> Slide:
        if len(self.deck.slides) <= 1:
            raise PptxError("cannot delete the last slide")
        slide = self.slide(index)
        pres_path = "ppt/presentation.xml"
        rels = self.archive.read_rels(pres_path)
        relation = next(
            (r for r in rels.values() if resolve_target(pres_path, r.target) == slide.path), None
        )
        if relation:
            xml = self.archive.read_text(pres_path) or ""
            xml = re.sub(rf'<p:sldId\b(?=[^>]*r:id="{re.escape(relation.id)}")[^>]*/>', "", xml)
            self.archive.write_text(pres_path, xml)
            rels.pop(relation.id, None)
            self.archive.write_rels(pres_path, rels)
        for path in [slide.path, rels_path_for(slide.path)]:
            self.archive.entries.pop(path, None)
        self.deck.slides.pop(index)
        return slide

    def move_slide(self, from_index: int, to_index: int) -> None:
        if not 0 <= to_index < len(self.deck.slides):
            raise PptxError("target slide index is out of range")
        slide = self.deck.slides.pop(from_index)
        self.deck.slides.insert(to_index, slide)
        pres_path = "ppt/presentation.xml"
        rels = self.archive.read_rels(pres_path)
        pres = self.archive.read_text(pres_path) or ""
        tags = re.findall(r"<p:sldId\b[^>]*/>", pres)
        mapped: dict[str, str] = {}
        for tag in tags:
            rid = re.search(r'r:id="([^"]+)"', tag)
            if rid:
                rel = rels.get(rid.group(1))
                if rel:
                    mapped[resolve_target(pres_path, rel.target)] = tag
        ordered = "".join(mapped.get(s.path, "") for s in self.deck.slides)
        pres = re.sub(r"(<p:sldIdLst>).*?(</p:sldIdLst>)", rf"\1{ordered}\2", pres, flags=re.S)
        self.archive.write_text(pres_path, pres)

    def set_slide_size(self, cx: int, cy: int) -> None:
        self.deck.size = SlideSize(cx, cy)
        xml = self.archive.read_text("ppt/presentation.xml") or ""
        xml = re.sub(r"<p:sldSz\b[^>]*/>", f'<p:sldSz cx="{cx}" cy="{cy}"/>', xml)
        self.archive.write_text("ppt/presentation.xml", xml)

    def require_element(self, slide_index: int, element_id: str) -> Element:
        element = element_by_id(self.slide(slide_index), element_id)
        if not element:
            raise PptxError(f"element {element_id} was not found on slide {slide_index}")
        return element

    def _add_media(self, slide: Slide, data: bytes, ext: str) -> tuple[str, str]:
        number = 1
        while f"ppt/media/image{number}.{ext}" in self.archive.entries:
            number += 1
        path = f"ppt/media/image{number}.{ext}"
        self.archive.entries[path] = data
        content_type = mimetypes.types_map.get("." + ext, f"image/{ext}")
        types = self.archive.read_text("[Content_Types].xml") or ""
        if not re.search(rf'<Default\b[^>]*Extension="{re.escape(ext)}"', types, re.I):
            types = types.replace(
                "</Types>", f'<Default Extension="{ext}" ContentType="{content_type}"/></Types>'
            )
            self.archive.write_text("[Content_Types].xml", types)
        relations = self.archive.read_rels(slide.path)
        used = [int(x[3:]) for x in relations if x.startswith("rId") and x[3:].isdigit()]
        r_id = f"rId{max(used, default=0) + 1}"
        relations[r_id] = Relationship(r_id, P_REL + "/image", relative_target(slide.path, path))
        self.archive.write_rels(slide.path, relations)
        return path, r_id

    def _next_slide_path(self) -> str:
        existing = [
            _as_int(m.group(1))
            for path in self.archive.entries
            for m in [re.match(r"ppt/slides/slide(\d+)\.xml$", path)]
            if m
        ]
        return f"ppt/slides/slide{max(existing, default=0) + 1}.xml"

    def _register_slide(self, after_index: int, new_path: str) -> Slide:
        pres_path = "ppt/presentation.xml"
        pres = self.archive.read_text(pres_path)
        if not pres:
            raise PptxError("presentation.xml is missing")
        rels = self.archive.read_rels(pres_path)
        used = [int(x[3:]) for x in rels if x.startswith("rId") and x[3:].isdigit()]
        r_id = f"rId{max(used, default=0) + 1}"
        rels[r_id] = Relationship(r_id, P_REL + "/slide", relative_target(pres_path, new_path))
        self.archive.write_rels(pres_path, rels)
        ids = [int(x) for x in re.findall(r'<p:sldId\b[^>]*\bid="(\d+)"', pres)]
        tag = f'<p:sldId id="{max(ids, default=255) + 1}" r:id="{r_id}"/>'
        anchor_slide = self.slide(after_index)
        anchor_rel = next(
            (r for r in rels.values() if resolve_target(pres_path, r.target) == anchor_slide.path),
            None,
        )
        if anchor_rel:
            pattern = rf'(<p:sldId\b(?=[^>]*r:id="{re.escape(anchor_rel.id)}")[^>]*/>)'
            pres = re.sub(pattern, rf"\1{tag}", pres, count=1)
        else:
            pres = pres.replace("</p:sldIdLst>", tag + "</p:sldIdLst>")
        self.archive.write_text(pres_path, pres)
        content = self.archive.read_text("[Content_Types].xml") or ""
        if f'PartName="/{new_path}"' not in content:
            content = content.replace(
                "</Types>",
                f'<Override PartName="/{new_path}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>',
            )
            self.archive.write_text("[Content_Types].xml", content)
        relations = {
            r.id: (r.target if r.target_mode == "External" else resolve_target(new_path, r.target))
            for r in self.archive.read_rels(new_path).values()
        }
        chain = self.archive.resolve_slide_chain(new_path)
        new_slide = parse_slide(
            self.archive.read_text(new_path) or "",
            new_path,
            self.deck.theme,
            relations,
            chain.get("layout_path"),
            chain.get("master_path"),
        )
        self.deck.slides.insert(after_index + 1, new_slide)
        return new_slide

    def slide_xml(self, slide: Slide) -> str:
        parts = [slide.body_prefix]
        for element in slide.elements:
            parts.append(element_xml(element))
            parts.append(element.anchor.gap_after)
        parts.append(slide.body_suffix)
        return "".join(parts)

    def to_bytes(self) -> bytes:
        for slide in self.deck.slides:
            if slide.is_dirty:
                self.archive.write_text(slide.path, self.slide_xml(slide))
        return self.archive.to_bytes()

    def save(self, output_path: str) -> None:
        Path(output_path).write_bytes(self.to_bytes())
        self.commit()

    def commit(self) -> None:
        """Reparse written slides and clear dirty state after a successful save."""
        new_archive = PackageArchive.open(self.archive.to_bytes())
        self.archive = new_archive
        self.deck = parse_deck(new_archive)


def _walk(elements: Iterable[Element]) -> Iterable[Element]:
    for element in elements:
        yield element
        yield from _walk(element.children)


def _remove(elements: list[Element], element_id: str) -> bool:
    for i, element in enumerate(elements):
        if element.id == element_id:
            del elements[i]
            return True
        if _remove(element.children, element_id):
            return True
    return False


def _blank_slide_xml() -> str:
    tree = '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree>'
    return (
        XML_DECL
        + f'<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"><p:cSld>{tree}</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def _blank_entries(width: int, height: int) -> dict[str, bytes]:
    tree = '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree>'
    content = (
        XML_DECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        + '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        + '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        + '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'
    )
    root_rels = (
        XML_DECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>'
    )
    presentation = (
        XML_DECL
        + f'<p:presentation xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst><p:sldSz cx="{width}" cy="{height}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    )
    presentation_rels = (
        XML_DECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>'
    )
    layout = (
        XML_DECL
        + f'<p:sldLayout xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}" type="blank"><p:cSld name="Blank">{tree}</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )
    master = (
        XML_DECL
        + f'<p:sldMaster xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"><p:cSld>{tree}</p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'
    )
    theme = (
        XML_DECL
        + f'<a:theme xmlns:a="{A_NS}" name="Office"><a:themeElements><a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'
    )
    return {
        "[Content_Types].xml": content.encode(),
        "_rels/.rels": root_rels.encode(),
        "ppt/presentation.xml": presentation.encode(),
        "ppt/_rels/presentation.xml.rels": presentation_rels.encode(),
        "ppt/slides/slide1.xml": _blank_slide_xml().encode(),
        "ppt/slides/_rels/slide1.xml.rels": (
            XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>'
        ).encode(),
        "ppt/slideLayouts/slideLayout1.xml": layout.encode(),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": (
            XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'
        ).encode(),
        "ppt/slideMasters/slideMaster1.xml": master.encode(),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": (
            XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>'
        ).encode(),
        "ppt/theme/theme1.xml": theme.encode(),
    }


# Python-friendly top-level compatibility entry points.
def open_pptx(data: bytes) -> PptxDocument:
    return PptxDocument.open_bytes(data)


def create_blank_pptx(width_emu: int = 12_192_000, height_emu: int = 6_858_000) -> bytes:
    return PptxDocument.create_blank(width_emu, height_emu).to_bytes()
