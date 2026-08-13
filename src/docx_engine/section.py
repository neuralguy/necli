"""Секции и параметры страницы."""

from __future__ import annotations

import re

from .xml_utils import escape_xml_attr

DEFAULT_SECTION = {
    "pageWidth": 11906,
    "pageHeight": 16838,
    "orientation": "portrait",
    "marginTop": 1440,
    "marginRight": 1440,
    "marginBottom": 1440,
    "marginLeft": 1440,
    "pageBorder": False,
    "columns": 1,
    "colSpace": 720,
    "headerDist": 720,
    "footerDist": 720,
}


def _int_attr(tag, name, fallback):
    m = re.search(rf'{re.escape(name)}="(-?\d+)"', tag)
    try:
        return int(m.group(1)) if m else fallback
    except ValueError:
        return fallback


def _v_align(xml):
    m = re.search(r'<w:vAlign\b[^>]*w:val="(center|both|bottom)"[^>]*/>', xml)
    return m.group(1) if m else None


def _has_visible_page_border(xml):
    m = re.search(r"<w:pgBorders[^>]*/>|<w:pgBorders[\s\S]*?</w:pgBorders>", xml)
    if not m:
        return False
    sides = re.findall(r"<w:(?:top|left|bottom|right)\b[^>]*/?>", m.group(0))
    for s in sides:
        val = re.search(r'w:val="([^"]*)"', s)
        if val and val.group(1) not in ("none", "nil"):
            return True
    return False


def section_settings_from_xml(xml: str) -> dict:
    pg_sz = re.search(r"<w:pgSz[^>]*/?>", xml)
    pg_sz = pg_sz.group(0) if pg_sz else ""
    pg_mar = re.search(r"<w:pgMar[^>]*/?>", xml)
    pg_mar = pg_mar.group(0) if pg_mar else ""
    cols = re.search(r"<w:cols[^>]*/?>", xml)
    cols = cols.group(0) if cols else ""
    doc_grid = None
    dg = re.search(r"<w:docGrid[^>]*/?>", xml)
    if dg:
        tag = dg.group(0)
        t = re.search(r'w:type="([^"]+)"', tag)
        lp = re.search(r'w:linePitch="(\d+)"', tag)
        cs = re.search(r'w:charSpace="(-?\d+)"', tag)
        grid_type = t.group(1) if t else "default"
        if grid_type not in ("default", "lines", "linesAndChars", "snapToChars"):
            grid_type = "default"
        doc_grid = {"type": grid_type}
        if lp:
            doc_grid["linePitch"] = int(lp.group(1))
        if cs:
            doc_grid["charSpace"] = int(cs.group(1))
    out = {
        "pageWidth": _int_attr(pg_sz, "w:w", DEFAULT_SECTION["pageWidth"]),
        "pageHeight": _int_attr(pg_sz, "w:h", DEFAULT_SECTION["pageHeight"]),
        "orientation": "landscape" if 'w:orient="landscape"' in pg_sz else "portrait",
        "marginTop": _int_attr(pg_mar, "w:top", 1440),
        "marginRight": _int_attr(pg_mar, "w:right", 1440),
        "marginBottom": _int_attr(pg_mar, "w:bottom", 1440),
        "marginLeft": _int_attr(pg_mar, "w:left", 1440),
        "headerDist": _int_attr(pg_mar, "w:header", 720),
        "footerDist": _int_attr(pg_mar, "w:footer", 720),
        "pageBorder": _has_visible_page_border(xml),
        "columns": _int_attr(cols, "w:num", 1),
        "colSpace": _int_attr(cols, "w:space", 720),
    }
    va = _v_align(xml)
    if va:
        out["vAlign"] = va
    if doc_grid:
        out["docGrid"] = doc_grid
    td = re.search(r'<w:textDirection[^>]*w:val="([^"]+)"', xml)
    if td and td.group(1) != "lrTb":
        out["textDirection"] = td.group(1)
    return out


def read_section_settings(parsed) -> dict:
    sect = next(
        (b for b in parsed.blocks if b.hidden and b.original_xml and "<w:sectPr" in b.original_xml),
        None,
    )
    return section_settings_from_xml(sect.original_xml if sect else "")


SECT_PR_RE = re.compile(r"<w:sectPr[^>]*/>|<w:sectPr[\s\S]*?</w:sectPr>")


def _hf_refs(xml, kind):
    refs = {}
    for ref in re.findall(rf"<w:{kind}Reference[^>]*/>", xml):
        t = re.search(r'w:type="(default|first|even)"', ref)
        rid = re.search(r'r:id="([^"]+)"', ref)
        if rid:
            refs[t.group(1) if t else "default"] = rid.group(1)
    return refs


def _section_from(sect_pr_xml, first_idx, last_idx):
    t = re.search(
        r'<w:type[^>]*w:val="(nextPage|continuous|evenPage|oddPage|nextColumn)"', sect_pr_xml
    )
    start = re.search(r'<w:pgNumType[^>]*w:start="(\d+)"', sect_pr_xml)
    fmt = re.search(r'<w:pgNumType[^>]*w:fmt="([^"]+)"', sect_pr_xml)
    st = t.group(1) if t else "nextPage"
    if st == "nextColumn":
        st = "continuous"
    info = {
        "settings": section_settings_from_xml(sect_pr_xml),
        "startType": st,
        "firstBlockIndex": first_idx,
        "lastBlockIndex": last_idx,
        "sectPrXml": sect_pr_xml,
        "titlePg": bool(re.search(r"<w:titlePg\s*/>", sect_pr_xml)),
        "headerRefs": _hf_refs(sect_pr_xml, "header"),
        "footerRefs": _hf_refs(sect_pr_xml, "footer"),
    }
    if start:
        info["pageNumberStart"] = int(start.group(1))
    if fmt:
        info["pageNumberFmt"] = fmt.group(1)
    return info


def read_sections(parsed) -> list[dict]:
    sections, first = [], 0
    for b in parsed.blocks:
        xml = b.original_xml or ""
        if b.docx_index is None or "<w:sectPr" not in xml:
            continue
        m = SECT_PR_RE.search(xml)
        if not m:
            continue
        sections.append(_section_from(m.group(0), first, b.docx_index))
        first = b.docx_index + 1
    if not sections:
        last = (
            parsed.blocks[-1].docx_index
            if parsed.blocks and parsed.blocks[-1].docx_index is not None
            else 0
        )
        sections.append(_section_from("", 0, last))
    return sections


def apply_page_num_type(sect_pr_xml, fmt=None, start=None):
    xml = sect_pr_xml
    if fmt is None and start is None:
        return re.sub(r"<w:pgNumType[^>]*/>", "", xml)
    existing = re.search(r"<w:pgNumType[^>]*/>", xml)
    tag = existing.group(0) if existing else "<w:pgNumType/>"

    def set_attr(src, name, value):
        src = re.sub(rf'\s{re.escape(name)}="[^"]*"', "", src)
        if value is None:
            return src
        return src[:-2] + f' {name}="{escape_xml_attr(str(value))}"/>'

    if fmt is not None:
        tag = set_attr(tag, "w:fmt", fmt)
    if start is not None:
        try:
            start = int(start)
        except (TypeError, ValueError) as exc:
            raise ValueError("page number start must be an integer") from exc
        if start < 0:
            raise ValueError("page number start must be non-negative")
        tag = set_attr(tag, "w:start", start)
    if existing:
        return xml[: existing.start()] + tag + xml[existing.end() :]
    if re.search(r"<w:cols[\s/>]", xml):
        return re.sub(r"(<w:cols[\s/>])", tag + r"\1", xml, count=1)
    if "<w:docGrid" in xml:
        return xml.replace("<w:docGrid", tag + "<w:docGrid", 1)
    return xml.replace("</w:sectPr>", tag + "</w:sectPr>", 1)


def apply_section_settings(sect_pr_xml: str, s: dict) -> str:
    xml = (
        sect_pr_xml
        or '<w:sectPr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"></w:sectPr>'
    )
    cur = section_settings_from_xml(xml)
    cfg = {**cur, **{k: v for k, v in (s or {}).items() if v is not None}}

    def as_int(key, minimum=None):
        try:
            v = int(cfg[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid section setting {key}") from exc
        if minimum is not None and v < minimum:
            raise ValueError(f"section setting {key} must be >= {minimum}")
        return v

    width, height = as_int("pageWidth", 1), as_int("pageHeight", 1)
    orientation = cfg.get("orientation", "portrait")
    if orientation not in ("portrait", "landscape"):
        raise ValueError("orientation must be portrait or landscape")
    # In Word, w:orient alone does not rotate the physical page dimensions.
    # Keep dimensions consistent with the requested orientation.
    if (orientation == "landscape" and width < height) or (
        orientation == "portrait" and width > height
    ):
        width, height = height, width
    orient = ' w:orient="landscape"' if orientation == "landscape" else ""
    pg_sz = f'<w:pgSz w:w="{width}" w:h="{height}"{orient}/>'
    if re.search(r"<w:pgSz[^>]*/?>", xml):
        xml = re.sub(r"<w:pgSz[^>]*/?>", pg_sz, xml, count=1)
    else:
        xml = re.sub(r"(<w:sectPr[^>]*>)", r"\1" + pg_sz, xml, count=1)

    def rep(tag, name, value):
        if f'{name}="' in tag:
            return re.sub(rf'{re.escape(name)}="-?\d+"', f'{name}="{value}"', tag)
        return tag[:-2] + f' {name}="{value}"/>'

    m = re.search(r"<w:pgMar[^>]*/>", xml)
    gutter = _int_attr(m.group(0), "w:gutter", 0) if m else 0
    pg_mar = (
        f'<w:pgMar w:top="{as_int("marginTop")}" w:right="{as_int("marginRight")}" '
        f'w:bottom="{as_int("marginBottom")}" w:left="{as_int("marginLeft")}" '
        f'w:header="{as_int("headerDist", 0)}" w:footer="{as_int("footerDist", 0)}" w:gutter="{gutter}"/>'
    )
    if m:
        xml = xml[: m.start()] + pg_mar + xml[m.end() :]
    else:
        anchor = re.search(
            r"<w:pgBorders\b|<w:lnNumType\b|<w:pgNumType\b|<w:cols\b|</w:sectPr>", xml
        )
        pos = anchor.start() if anchor else len(xml)
        xml = xml[:pos] + pg_mar + xml[pos:]
    xml = re.sub(r"<w:pgBorders[^>]*/>|<w:pgBorders[\s\S]*?</w:pgBorders>", "", xml)
    if cfg.get("pageBorder"):

        def side(n):
            return f'<w:{n} w:val="single" w:sz="4" w:space="24" w:color="auto"/>'

        borders = (
            '<w:pgBorders w:offsetFrom="page">'
            + side("top")
            + side("left")
            + side("bottom")
            + side("right")
            + "</w:pgBorders>"
        )
        xml = re.sub(r"(<w:pgMar[^>]*/>)", r"\1" + borders, xml, count=1)
    columns = as_int("columns", 1)
    col_space = as_int("colSpace", 0)
    num_attr = f' w:num="{columns}"' if columns > 1 else ""
    cm = re.search(r"<w:cols[^>]*/>|<w:cols[^>]*>[\s\S]*?</w:cols>", xml)
    if cm:
        open_tag = re.match(r"<w:cols[^>]*>", cm.group(0))
        open_tag = open_tag.group(0) if open_tag else cm.group(0)
        cur = re.search(r' w:num="(\d+)"', open_tag)
        cur = cur.group(1) if cur else "1"
        if not cm.group(0).endswith("/>") and cur == str(columns):
            tag = re.sub(r'\sw:space="-?\d+"', "", open_tag)
            tag = tag[:-1] + f' w:space="{col_space}">'
            if columns > 1 and not re.search(r'\sw:num="', tag):
                tag = tag[:-1] + f' w:num="{columns}">'
            xml = xml[: cm.start()] + tag + cm.group(0)[len(open_tag) :] + xml[cm.end() :]
        else:
            tag = f'<w:cols{num_attr} w:space="{col_space}"/>'
            xml = xml[: cm.start()] + tag + xml[cm.end() :]
    else:
        anchor = re.search(r"(<w:pgBorders[\s\S]*?</w:pgBorders>|<w:pgMar[^>]*/>)", xml)
        cols = f'<w:cols{num_attr} w:space="{col_space}"/>'
        if anchor:
            xml = xml.replace(anchor.group(0), anchor.group(0) + cols, 1)
        else:
            xml = xml.replace("</w:sectPr>", cols + "</w:sectPr>", 1)

    def replace_optional(tag_name, replacement, anchors=()):
        nonlocal xml
        xml = re.sub(
            rf"<w:{tag_name}\b[^>]*/>|<w:{tag_name}\b[^>]*>[\s\S]*?</w:{tag_name}>", "", xml
        )
        if not replacement:
            return
        for anchor_name in anchors:
            m = re.search(rf"<w:{anchor_name}\b", xml)
            if m:
                xml = xml[: m.start()] + replacement + xml[m.start() :]
                return
        xml = xml.replace("</w:sectPr>", replacement + "</w:sectPr>", 1)

    if "vAlign" in s:
        va = s.get("vAlign")
        if va is not None and va not in ("top", "center", "both", "bottom"):
            raise ValueError("invalid vAlign")
        # OOXML's default/top is represented by absence.
        replace_optional(
            "vAlign",
            None if va in (None, "top") else f'<w:vAlign w:val="{va}"/>',
            ("titlePg", "textDirection", "docGrid"),
        )
    if "textDirection" in s:
        td = s.get("textDirection")
        replace_optional(
            "textDirection",
            None
            if td in (None, "lrTb")
            else f'<w:textDirection w:val="{escape_xml_attr(str(td))}"/>',
            ("docGrid",),
        )
    if "docGrid" in s:
        dg = s.get("docGrid")
        replacement = None
        if dg:
            typ = dg.get("type", "default")
            if typ not in ("default", "lines", "linesAndChars", "snapToChars"):
                raise ValueError("invalid docGrid type")
            attrs = [] if typ == "default" else [f'w:type="{typ}"']
            if dg.get("linePitch") is not None:
                attrs.append(f'w:linePitch="{int(dg["linePitch"])}"')
            if dg.get("charSpace") is not None:
                attrs.append(f'w:charSpace="{int(dg["charSpace"])}"')
            replacement = f"<w:docGrid{' ' if attrs else ''}{' '.join(attrs)}/>"
        replace_optional("docGrid", replacement)
    return xml


def apply_section_start_type(sect_pr_xml, type_):
    if type_ not in ("nextPage", "continuous", "evenPage", "oddPage", "nextColumn"):
        raise ValueError("invalid section start type")
    xml = re.sub(r"<w:type[^>]*/>", "", sect_pr_xml)
    if type_ == "nextPage":
        return xml
    tag = f'<w:type w:val="{type_}"/>'
    if "<w:pgSz" in xml:
        return xml.replace("<w:pgSz", tag + "<w:pgSz", 1)
    return re.sub(r"(<w:sectPr[^>]*>)", r"\1" + tag, xml, count=1)


def read_page_color(parsed):
    m = re.search(r'<w:background[^>]*w:color="([0-9A-Fa-f]{6})"', parsed.internal["documentXml"])
    return m.group(1).upper() if m else None
