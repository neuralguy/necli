"""Parsing of PPTX slide XML into the core model.

The parser is intentionally conservative. Unsupported OOXML remains a
``passthrough`` element with its exact source XML, rather than being discarded.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import replace
from typing import Any
from xml.etree import ElementTree as ET

from .archive import PackageArchive, resolve_target
from .models import (
    ByteAnchor,
    Deck,
    Element,
    EmuRect,
    Paragraph,
    Slide,
    TextBody,
    TextRun,
    Transform,
)
from .xmlutil import (
    A_NS,
    C_NS,
    NS,
    R_NS,
    attr_float,
    attr_int,
    colour_from_node,
    local,
    parse_xml,
    qn,
    tag_ranges,
)

_FRAGMENT_PREFIX = f'<root xmlns:p="{NS["p"]}" xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:c="{C_NS}">'
_FRAGMENT_SUFFIX = "</root>"


def parse_theme(xml: str | None) -> dict[str, str]:
    if not xml:
        return {}
    try:
        root = parse_xml(xml)
    except ET.ParseError:
        return {}
    out: dict[str, str] = {}
    scheme = root.find(".//a:clrScheme", NS)
    if scheme is None:
        return out
    for node in scheme:
        name = local(node.tag)
        out[name] = colour_from_node(node, out, "000000").lstrip("#")[:6]
    return out


def _fragment(xml: str) -> ET.Element:
    return ET.fromstring((_FRAGMENT_PREFIX + xml + _FRAGMENT_SUFFIX).encode("utf-8"))


def _find_xfrm(node: ET.Element) -> Transform:
    xfrm = node.find(".//a:xfrm", NS)
    if xfrm is None:
        xfrm = node.find(".//p:xfrm", NS)
    if xfrm is None:
        return Transform()
    off = xfrm.find("a:off", NS)
    ext = xfrm.find("a:ext", NS)
    return Transform(
        EmuRect(attr_int(off, "x"), attr_int(off, "y"), attr_int(ext, "cx"), attr_int(ext, "cy")),
        attr_int(xfrm, "rot"),
        xfrm.get("flipH") in {"1", "true"},
        xfrm.get("flipV") in {"1", "true"},
    )


def _parse_fill(parent: ET.Element | None, theme: dict[str, str]) -> dict[str, Any] | None:
    if parent is None:
        return None
    no_fill = parent.find("a:noFill", NS)
    if no_fill is not None:
        return {"type": "none"}
    solid = parent.find("a:solidFill", NS)
    if solid is not None:
        return {"type": "solid", "color": colour_from_node(solid, theme)}
    grad = parent.find("a:gradFill", NS)
    if grad is not None:
        stops: list[dict[str, Any]] = []
        for gs in grad.findall(".//a:gs", NS):
            stops.append(
                {"pos": attr_float(gs, "pos") / 100000.0, "color": colour_from_node(gs, theme)}
            )
        lin = grad.find("a:lin", NS)
        path = grad.find("a:path", NS)
        return {
            "type": "gradient",
            "stops": stops,
            "angle": attr_float(lin, "ang") / 60000.0 if lin is not None else 0.0,
            "path": path.get("path") if path is not None else None,
        }
    blip = parent.find("a:blipFill", NS)
    if blip is not None:
        embedded = blip.find("a:blip", NS)
        rel_id = embedded.get(qn("r", "embed")) if embedded is not None else None
        return {"type": "image", "rel_id": rel_id}
    patt = parent.find("a:pattFill", NS)
    if patt is not None:
        return {
            "type": "pattern",
            "preset": patt.get("prst", "pct5"),
            "fg": colour_from_node(patt.find("a:fgClr", NS), theme),
            "bg": colour_from_node(patt.find("a:bgClr", NS), theme),
        }
    return None


def _parse_stroke(parent: ET.Element | None, theme: dict[str, str]) -> dict[str, Any] | None:
    if parent is None:
        return None
    line = parent.find("a:ln", NS)
    if line is None:
        return None
    fill = _parse_fill(line, theme) or {"type": "none"}
    stroke: dict[str, Any] = {
        "fill": fill,
        "width": attr_int(line, "w", 12700),
        "dash": None,
        "cap": line.get("cap"),
    }
    dash = line.find("a:prstDash", NS)
    if dash is not None:
        stroke["dash"] = dash.get("val")
    for source, key in (("a:headEnd", "head_end"), ("a:tailEnd", "tail_end")):
        end = line.find(source, NS)
        if end is not None and end.get("type") not in {None, "none"}:
            stroke[key] = {"type": end.get("type"), "w": end.get("w"), "len": end.get("len")}
    return stroke


def _parse_run(node: ET.Element, theme: dict[str, str]) -> TextRun:
    rpr = node.find("a:rPr", NS)
    text_node = node.find("a:t", NS)
    text = ("\n" if local(node.tag) == "br" else "") if text_node is None else text_node.text or ""
    font_size = attr_float(rpr, "sz") / 100.0 if rpr is not None and rpr.get("sz") else None
    latin = rpr.find("a:latin", NS) if rpr is not None else None
    color_fill = rpr.find("a:solidFill", NS) if rpr is not None else None
    hyperlink = None
    if rpr is not None:
        hlink = rpr.find("a:hlinkClick", NS)
        if hlink is not None:
            hyperlink = hlink.get(qn("r", "id")) or hlink.get("action")
    return TextRun(
        text=text,
        bold=(rpr.get("b") in {"1", "true"})
        if rpr is not None and rpr.get("b") is not None
        else None,
        italic=(rpr.get("i") in {"1", "true"})
        if rpr is not None and rpr.get("i") is not None
        else None,
        underline=(rpr.get("u") not in {None, "none"}) if rpr is not None else None,
        strike=(rpr.get("strike") not in {None, "noStrike"}) if rpr is not None else None,
        font_size=font_size,
        font_family=latin.get("typeface") if latin is not None else None,
        color=colour_from_node(color_fill, theme) if color_fill is not None else None,
        hyperlink=hyperlink,
        baseline=attr_float(rpr, "baseline") / 1000.0
        if rpr is not None and rpr.get("baseline")
        else None,
        letter_spacing=attr_float(rpr, "spc") / 100.0
        if rpr is not None and rpr.get("spc")
        else None,
        field=node.get("type") if local(node.tag) == "fld" else None,
    )


def _parse_paragraph(node: ET.Element, theme: dict[str, str]) -> Paragraph:
    ppr = node.find("a:pPr", NS)
    align_map = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}
    paragraph = Paragraph(
        align=align_map.get(ppr.get("algn")) if ppr is not None else None,
        level=attr_int(ppr, "lvl") if ppr is not None and ppr.get("lvl") else None,
        mar_l=attr_int(ppr, "marL") if ppr is not None and ppr.get("marL") else None,
        indent=attr_int(ppr, "indent") if ppr is not None and ppr.get("indent") else None,
    )
    if ppr is not None:
        line = ppr.find("a:lnSpc/a:spcPct", NS)
        exact = ppr.find("a:lnSpc/a:spcPts", NS)
        before = ppr.find("a:spcBef/a:spcPts", NS)
        after = ppr.find("a:spcAft/a:spcPts", NS)
        if line is not None:
            paragraph.line_height = attr_float(line, "val") / 1000.0
        if exact is not None:
            paragraph.line_exact = attr_float(exact, "val") / 100.0
        if before is not None:
            paragraph.space_before = attr_float(before, "val") / 100.0
        if after is not None:
            paragraph.space_after = attr_float(after, "val") / 100.0
        bu_none, bu_char, bu_num = (
            ppr.find("a:buNone", NS),
            ppr.find("a:buChar", NS),
            ppr.find("a:buAutoNum", NS),
        )
        if bu_none is not None:
            paragraph.bullet = {"type": "none"}
        elif bu_char is not None:
            paragraph.bullet = {"type": "char", "char": bu_char.get("char", "•")}
        elif bu_num is not None:
            paragraph.bullet = {"type": "number", "num_type": bu_num.get("type", "arabicPeriod")}
    for child in node:
        if local(child.tag) in {"r", "fld", "br"}:
            paragraph.runs.append(_parse_run(child, theme))
    if not paragraph.runs:
        paragraph.runs.append(TextRun(""))
    return paragraph


def _parse_text(node: ET.Element, theme: dict[str, str]) -> TextBody | None:
    tx_body = node.find("p:txBody", NS)
    if tx_body is None:
        tx_body = node.find("a:txBody", NS)
    if tx_body is None:
        return None
    body_pr = tx_body.find("a:bodyPr", NS)
    anchor = (
        {"t": "top", "ctr": "middle", "b": "bottom"}.get(body_pr.get("anchor"))
        if body_pr is not None
        else None
    )
    insets = None
    if body_pr is not None:
        insets = {
            "l": attr_int(body_pr, "lIns", 91440),
            "t": attr_int(body_pr, "tIns", 45720),
            "r": attr_int(body_pr, "rIns", 91440),
            "b": attr_int(body_pr, "bIns", 45720),
        }
    autofit = "none"
    # DrawingML stores autofit inside a:bodyPr, not beside it in a:txBody.
    if body_pr is not None and body_pr.find("a:normAutofit", NS) is not None:
        autofit = "shrink"
    elif body_pr is not None and body_pr.find("a:spAutoFit", NS) is not None:
        autofit = "resize"
    return TextBody(
        [_parse_paragraph(p, theme) for p in tx_body.findall("a:p", NS)],
        anchor,
        insets,
        autofit,
        (body_pr.get("wrap") != "none") if body_pr is not None else True,
    )


def _non_visual(node: ET.Element) -> tuple[str, str | None, str | None, str | None]:
    c_nv = node.find(".//p:cNvPr", NS)
    nv_id = c_nv.get("id") if c_nv is not None else None
    name = c_nv.get("name") if c_nv is not None else None
    descr = c_nv.get("descr") if c_nv is not None else None
    # use stable cNvPr id; fall back only for malformed parts
    return nv_id or "unknown", name, descr, nv_id


def _parse_shape(
    node: ET.Element, anchor: ByteAnchor, theme: dict[str, str], relations: dict[str, str]
) -> Element:
    identifier, name, descr, nv_id = _non_visual(node)
    sp_pr = node.find("p:spPr", NS)
    preset = None
    adjust: dict[str, int] = {}
    if sp_pr is not None:
        geometry = sp_pr.find("a:prstGeom", NS)
        if geometry is not None:
            preset = geometry.get("prst")
            for guide in geometry.findall(".//a:gd", NS):
                with contextlib.suppress(ValueError):
                    adjust[guide.get("name", "adj")] = int(guide.get("fmla", "val 0").split()[-1])
    placeholder_node = node.find(".//p:ph", NS)
    return Element(
        identifier,
        "shape" if preset else "text",
        anchor,
        _find_xfrm(sp_pr if sp_pr is not None else node),
        name,
        descr,
        placeholder_node.get("type") if placeholder_node is not None else None,
        nv_id,
        preset,
        adjust,
        _parse_fill(sp_pr, theme),
        _parse_stroke(sp_pr, theme),
        text=_parse_text(node, theme),
    )


def _parse_picture(
    node: ET.Element, anchor: ByteAnchor, theme: dict[str, str], relations: dict[str, str]
) -> Element:
    identifier, name, descr, nv_id = _non_visual(node)
    blip_fill = node.find("p:blipFill", NS)
    blip = blip_fill.find("a:blip", NS) if blip_fill is not None else None
    r_id = blip.get(qn("r", "embed")) if blip is not None else None
    media_ref = relations.get(r_id or "")
    src = blip_fill.find("a:srcRect", NS) if blip_fill is not None else None
    src_rect = None
    if src is not None:
        src_rect = {key: attr_float(src, key) / 100000.0 for key in ("l", "t", "r", "b")}
    opacity = None
    alpha = blip.find("a:alphaModFix", NS) if blip is not None else None
    if alpha is not None:
        opacity = attr_float(alpha, "amt", 100000) / 100000.0
    sp_pr = node.find("p:spPr", NS)
    return Element(
        identifier,
        "picture",
        anchor,
        _find_xfrm(sp_pr if sp_pr is not None else node),
        name,
        descr,
        nv_id=nv_id,
        preset_geometry=(
            sp_pr.find("a:prstGeom", NS).get("prst")
            if sp_pr is not None and sp_pr.find("a:prstGeom", NS) is not None
            else None
        ),
        fill=_parse_fill(sp_pr, theme),
        stroke=_parse_stroke(sp_pr, theme),
        media_ref=media_ref,
        media_r_id=r_id,
        src_rect=src_rect,
        opacity=opacity,
    )


def _parse_table(node: ET.Element, anchor: ByteAnchor, theme: dict[str, str]) -> Element:
    identifier, name, descr, nv_id = _non_visual(node)
    tbl = node.find(".//a:tbl", NS)
    if tbl is None:
        return Element(
            identifier,
            "passthrough",
            anchor,
            _find_xfrm(node),
            name,
            descr,
            nv_id=nv_id,
            kind="table",
        )
    widths = [attr_int(col, "w") for col in tbl.findall("a:tblGrid/a:gridCol", NS)]
    heights: list[int] = []
    rows: list[list[dict[str, Any]]] = []
    for tr in tbl.findall("a:tr", NS):
        heights.append(attr_int(tr, "h"))
        parsed_row: list[dict[str, Any]] = []
        for tc in tr.findall("a:tc", NS):
            cell_body = _parse_text(tc, theme)
            tcpr = tc.find("a:tcPr", NS)
            parsed_row.append(
                {
                    "text": cell_body,
                    "fill": _parse_fill(tcpr, theme),
                    "grid_span": attr_int(tc, "gridSpan", 1),
                    "row_span": attr_int(tc, "rowSpan", 1),
                    "merged": tc.get("hMerge") in {"1", "true"}
                    or tc.get("vMerge") in {"1", "true"},
                }
            )
        rows.append(parsed_row)
    return Element(
        identifier,
        "table",
        anchor,
        _find_xfrm(node),
        name,
        descr,
        nv_id=nv_id,
        col_widths=widths,
        row_heights=heights,
        rows=rows,
    )


def _parse_chart(chart_xml: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "unknown", "series": [], "title": None}
    if not chart_xml:
        return result
    try:
        root = parse_xml(chart_xml)
    except ET.ParseError:
        return result
    title = root.find(".//c:title//a:t", NS)
    result["title"] = title.text if title is not None else None
    plot = root.find(".//c:plotArea", NS)
    if plot is None:
        return result
    for chart in plot:
        name = local(chart.tag)
        if name.endswith("Chart"):
            result["kind"] = name[:-5]
            for ser in chart.findall("c:ser", NS):
                series: dict[str, Any] = {"name": None, "categories": [], "values": []}
                tx = ser.find(".//c:tx//c:v", NS)
                series["name"] = tx.text if tx is not None else None
                series["categories"] = [
                    (n.text or "") for n in ser.findall(".//c:cat//c:pt//c:v", NS)
                ]
                values: list[float | str] = []
                for n in ser.findall(".//c:val//c:pt//c:v", NS):
                    try:
                        values.append(float(n.text or "0"))
                    except ValueError:
                        values.append(n.text or "")
                series["values"] = values
                result["series"].append(series)
            break
    return result


def _parse_group(
    node: ET.Element, anchor: ByteAnchor, theme: dict[str, str], relations: dict[str, str]
) -> Element:
    identifier, name, descr, nv_id = _non_visual(node)
    group_pr = node.find("p:grpSpPr", NS)
    children: list[Element] = []
    for index, child in enumerate(list(node)):
        kind = local(child.tag)
        if kind in {"nvGrpSpPr", "grpSpPr"}:
            continue
        raw = ET.tostring(child, encoding="unicode")
        child_anchor = ByteAnchor(index, raw, (0, len(raw)))
        parsed = _parse_node(child, child_anchor, theme, relations)
        if parsed:
            children.append(parsed)
    child_xfrm = group_pr.find("a:xfrm", NS) if group_pr is not None else None
    child_off, child_ext = (
        child_xfrm.find("a:chOff", NS) if child_xfrm is not None else None,
        child_xfrm.find("a:chExt", NS) if child_xfrm is not None else None,
    )
    child_offset = (
        EmuRect(
            attr_int(child_off, "x"),
            attr_int(child_off, "y"),
            attr_int(child_ext, "cx"),
            attr_int(child_ext, "cy"),
        )
        if child_xfrm is not None
        else None
    )
    return Element(
        identifier,
        "group",
        anchor,
        _find_xfrm(group_pr if group_pr is not None else node),
        name,
        descr,
        nv_id=nv_id,
        children=children,
        child_offset=child_offset,
    )


def _parse_node(
    node: ET.Element, anchor: ByteAnchor, theme: dict[str, str], relations: dict[str, str]
) -> Element | None:
    kind = local(node.tag)
    if kind == "sp":
        return _parse_shape(node, anchor, theme, relations)
    if kind == "pic":
        return _parse_picture(node, anchor, theme, relations)
    if kind == "grpSp":
        return _parse_group(node, anchor, theme, relations)
    if kind == "graphicFrame":
        if node.find(".//a:tbl", NS) is not None:
            return _parse_table(node, anchor, theme)
        identifier, name, descr, nv_id = _non_visual(node)
        chart_node = node.find(".//c:chart", NS)
        if chart_node is not None:
            r_id = chart_node.get(qn("r", "id"))
            return Element(
                identifier,
                "chart",
                anchor,
                _find_xfrm(node),
                name,
                descr,
                nv_id=nv_id,
                chart=_parse_chart(relations.get(r_id or "")),
            )
        return Element(
            identifier,
            "passthrough",
            anchor,
            _find_xfrm(node),
            name,
            descr,
            nv_id=nv_id,
            kind="smartart",
        )
    if kind == "cxnSp":
        identifier, name, descr, nv_id = _non_visual(node)
        return Element(
            identifier,
            "passthrough",
            anchor,
            _find_xfrm(node),
            name,
            descr,
            nv_id=nv_id,
            kind="connector",
        )
    return None


def _parse_background(xml: str, theme: dict[str, str]) -> dict[str, Any] | None:
    """Извлекает заливку <p:bg> (если есть) из XML слайда."""
    bg = re.search(r"<p:bg\b[^>]*>.*?</p:bg>", xml, re.S)
    if not bg:
        return None
    try:
        node = _fragment(bg.group(0))
    except ET.ParseError:
        return None
    bg_pr = node.find(".//p:bgPr", NS)
    return _parse_fill(bg_pr, theme) if bg_pr is not None else None


def parse_slide(
    xml: str,
    path: str,
    theme: dict[str, str],
    relations: dict[str, str],
    layout_path: str | None = None,
    master_path: str | None = None,
) -> Slide:
    background = _parse_background(xml, theme)
    sp_tree_match = re.search(r"<p:spTree\b[^>]*>", xml)
    if not sp_tree_match:
        return Slide(path, xml, xml, "", [], layout_path, master_path, background=background)
    # find the matching spTree closing token. It is the only such subtree in a slide.
    close = xml.find("</p:spTree>", sp_tree_match.end())
    if close < 0:
        return Slide(path, xml, xml, "", [], layout_path, master_path, background=background)
    close + len("</p:spTree>")
    direct = tag_ranges(
        xml, ("p:sp", "p:pic", "p:grpSp", "p:graphicFrame", "p:cxnSp"), sp_tree_match.end(), close
    )
    if not direct:
        # Keep the opening spTree and its mandatory group properties in the
        # prefix, but leave the closing tag in the suffix. New elements must
        # be inserted *inside* p:spTree, not after it.
        return Slide(
            path, xml, xml[:close], xml[close:], [], layout_path, master_path, background=background
        )
    elements: list[Element] = []
    for i, (_, start, end) in enumerate(direct):
        raw = xml[start:end]
        # The closing </p:spTree> belongs to body_suffix, never to the last
        # element's gap. Otherwise an element appended in a later edit is
        # serialized after </p:spTree> and silently disappears in Office.
        gap_after = xml[end : direct[i + 1][1] if i + 1 < len(direct) else close]
        try:
            parsed = _parse_node(
                _fragment(raw)[0], ByteAnchor(i, raw, (start, end), gap_after), theme, relations
            )
        except (ET.ParseError, IndexError):
            parsed = Element(
                f"unknown-{i}",
                "passthrough",
                ByteAnchor(i, raw, (start, end), gap_after),
                kind="unknown",
            )
        if parsed:
            elements.append(parsed)
    return Slide(
        path,
        xml,
        xml[: direct[0][1]],
        xml[close:],
        elements,
        layout_path,
        master_path,
        background=background,
    )


def _placeholder_transforms(xml: str | None) -> dict[str, Transform]:
    """Extract geometry declared by a layout or master placeholder."""
    if not xml:
        return {}
    try:
        root = parse_xml(xml)
    except ET.ParseError:
        return {}
    result: dict[str, Transform] = {}
    for shape in root.findall(".//p:sp", NS):
        ph = shape.find(".//p:ph", NS)
        if ph is None:
            continue
        kind = ph.get("type", "body")
        sp_pr = shape.find("p:spPr", NS)
        transform = _find_xfrm(sp_pr if sp_pr is not None else shape)
        if transform.offset.cx or transform.offset.cy:
            result.setdefault(kind, transform)
    return result


def _apply_placeholder_geometry(
    slide: Slide, layout_xml: str | None, master_xml: str | None
) -> None:
    """Use layout, then master, geometry for zero-sized slide placeholders."""
    layout = _placeholder_transforms(layout_xml)
    master = _placeholder_transforms(master_xml)
    for element in slide.elements:
        if not element.placeholder or element.transform.offset.cx or element.transform.offset.cy:
            continue
        inherited = layout.get(element.placeholder) or master.get(element.placeholder)
        if inherited:
            element.transform = replace(inherited)


def parse_deck(archive: PackageArchive) -> Deck:
    size, paths = archive.read_presentation()
    first_chain = archive.resolve_slide_chain(paths[0]) if paths else {"theme_path": None}
    theme = parse_theme(archive.read_text(first_chain.get("theme_path") or ""))
    slides: list[Slide] = []
    for path in paths:
        xml = archive.read_text(path)
        if xml is None:
            continue
        chain = archive.resolve_slide_chain(path)
        relations: dict[str, str] = {}
        for relation in archive.read_rels(path).values():
            relations[relation.id] = (
                relation.target
                if relation.target_mode == "External"
                else resolve_target(path, relation.target)
            )
        slide = parse_slide(
            xml, path, theme, relations, chain.get("layout_path"), chain.get("master_path")
        )
        _apply_placeholder_geometry(
            slide,
            archive.read_text(chain.get("layout_path") or ""),
            archive.read_text(chain.get("master_path") or ""),
        )
        slides.append(slide)
    return Deck(slides, size, archive.original_hash, theme)
