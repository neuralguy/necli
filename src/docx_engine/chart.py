"""Чарты: парсинг/сборка/патч chart-партов + встроенный xlsx."""

from __future__ import annotations

import base64
import io
import math
import re
import zipfile

from .xml_utils import (
    attrs_of,
    children_of,
    escape_xml_attr,
    escape_xml_text,
    find_child,
    find_children,
    name_of,
    text_of,
)
from .xml_utils import parse as xml_parse

CHART_KINDS = {
    "c:barChart": "bar",
    "c:bar3DChart": "bar",
    "c:lineChart": "line",
    "c:line3DChart": "line",
    "c:pieChart": "pie",
    "c:pie3DChart": "pie",
    "c:doughnutChart": "pie",
    "c:areaChart": "area",
    "c:area3DChart": "area",
}
CHART_WORKBOOK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
)
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
MAX_XLSX_ROWS = 1_048_576
MAX_XLSX_COLS = 16_384


def _cache_points(container):
    ref = find_child(container, "c:strRef") or find_child(container, "c:numRef")
    cache = (
        (find_child(ref, "c:strCache") or find_child(ref, "c:numCache"))
        if ref
        else (find_child(container, "c:strLit") or find_child(container, "c:numLit"))
    )
    if not cache:
        return []
    try:
        count = int(attrs_of(find_child(cache, "c:ptCount") or {}).get("val") or 0)
    except (TypeError, ValueError):
        count = 0
    count = max(0, min(count, MAX_XLSX_ROWS))
    points = {}
    for pt in find_children(cache, "c:pt"):
        try:
            idx = int(attrs_of(pt).get("idx", ""))
        except ValueError:
            continue
        if idx < 0:
            continue
        points[idx] = text_of(find_child(pt, "c:v") or {})
    length = min(MAX_XLSX_ROWS, max(count, (max(points) + 1) if points else 0))
    return [points.get(i) for i in range(length)]


def _series_name(ser):
    tx = find_child(ser, "c:tx")
    if not tx:
        return None
    lit = find_child(tx, "c:v")
    if lit:
        return text_of(lit)
    cached = _cache_points(tx)
    return cached[0] if cached else None


def _chart_title(chart):
    title = find_child(chart, "c:title")
    if not title:
        return None
    texts = []

    def walk(node):
        for c in children_of(node):
            if name_of(c) == "a:t":
                texts.append(text_of(c))
            else:
                walk(c)

    walk(title)
    return "".join(texts)


def parse_chart_part_xml(xml: str, part_path: str):
    parsed = xml_parse(xml)
    space = next((n for n in parsed if name_of(n) == "c:chartSpace"), None)
    chart = find_child(space, "c:chart") if space else None
    plot_area = find_child(chart, "c:plotArea") if chart else None
    if not chart or not plot_area:
        return None
    plot = next(
        (c for c in children_of(plot_area) if (name_of(c) or "").endswith("Chart")),
        None,
    )
    if not plot:
        return None
    kind = CHART_KINDS.get(name_of(plot) or "", "other")
    categories, series = [], []
    for ser in find_children(plot, "c:ser"):
        val = find_child(ser, "c:val")
        raw = _cache_points(val) if val else []
        values = []
        for v in raw:
            if v is None or str(v).strip() == "":
                values.append(None)
                continue
            try:
                values.append(float(v))
            except ValueError:
                values.append(None)
        if not values:
            continue
        cat = find_child(ser, "c:cat")
        if cat and not categories:
            categories = [v or "" for v in _cache_points(cat)]
        name = _series_name(ser)
        s = {"values": values}
        if name is not None:
            s["name"] = name
        series.append(s)
    if not series:
        return None
    title = _chart_title(chart)
    out = {
        "partPath": part_path,
        "kind": kind,
        "categories": categories,
        "series": series,
    }
    if title is not None:
        out["title"] = title
    return out


def _xlsx_col(i):
    """Zero-based Excel column index -> A..XFD."""
    if not isinstance(i, int) or i < 0 or i >= MAX_XLSX_COLS:
        raise ValueError(f"Excel column index out of range: {i}")
    n = i + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _col_letter(i):
    # Series start in worksheet column B (A contains categories).
    return _xlsx_col(i + 1)


# Standard Office accent palette. Explicit fills are needed: some renderers
# (e.g. LibreOffice) leave bars/slices without fill when colors are missing.
ACCENT_COLORS = ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"]


def _series_sp_pr(i: int) -> str:
    color = ACCENT_COLORS[i % len(ACCENT_COLORS)]
    return (
        f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        f'<a:ln><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr>'
    )


def _pie_data_points_xml(values) -> str:
    """Explicit per-slice fills for pie charts: varyColors alone is not enough
    for LibreOffice to color slices, so emit c:dPt entries per point."""
    dpts = []
    for i, v in enumerate(values):
        if v is None:
            continue
        color = ACCENT_COLORS[i % len(ACCENT_COLORS)]
        dpts.append(
            f'<c:dPt><c:idx val="{i}"/>'
            f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            f'<a:ln><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr>'
            f"</c:dPt>"
        )
    return "".join(dpts)


def _str_cache_xml(values, f):
    return (
        f'<c:strRef><c:f>{escape_xml_text(f)}</c:f><c:strCache><c:ptCount val="{len(values)}"/>'
        + "".join(
            f'<c:pt idx="{i}"><c:v>{escape_xml_text(str(v))}</c:v></c:pt>'
            for i, v in enumerate(values)
        )
        + "</c:strCache></c:strRef>"
    )


def _num_text(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(n):
        return None
    return str(v)


def _num_cache_xml(values, f):
    points = []
    for i, v in enumerate(values):
        text = _num_text(v)
        if text is not None:
            points.append(f'<c:pt idx="{i}"><c:v>{escape_xml_text(text)}</c:v></c:pt>')
    return (
        f"<c:numRef><c:f>{escape_xml_text(f)}</c:f><c:numCache><c:formatCode>General</c:formatCode>"
        f'<c:ptCount val="{len(values)}"/>'
        + "".join(points)
        + "</c:numCache></c:numRef>"
    )


def build_chart_part_xml(chart: dict, external_data_r_id=None) -> str:
    kind = chart.get("kind", "line")
    if kind not in ("bar", "line", "pie", "area"):
        raise ValueError(f"unsupported chart kind: {kind}")
    categories = list(chart.get("categories") or [])
    if len(categories) > MAX_XLSX_ROWS - 1:
        raise ValueError("too many chart categories for an XLSX worksheet")
    series = list(chart.get("series") or [])
    if len(series) > MAX_XLSX_COLS - 1:
        raise ValueError("too many chart series for an XLSX worksheet")
    rows = len(categories)
    sers = []
    for i, ser in enumerate(series):
        col = _col_letter(i)
        name = ser.get("name") if isinstance(ser, dict) else None
        name = str(name) if name is not None else f"Series {i + 1}"
        values = list(ser.get("values") or [])[:rows]
        if len(values) < rows:
            values.extend([None] * (rows - len(values)))
        dpts = _pie_data_points_xml(values) if kind == "pie" else ""
        sers.append(
            f'<c:ser><c:idx val="{i}"/><c:order val="{i}"/>'
            f"<c:tx>{_str_cache_xml([name], f'Sheet1!${col}$1')}</c:tx>"
            f"{_series_sp_pr(i)}"
            f"{dpts}"
            f"<c:cat>{_str_cache_xml(categories, f'Sheet1!$A$2:$A${rows + 1}')}</c:cat>"
            f"<c:val>{_num_cache_xml(values, f'Sheet1!${col}$2:${col}${rows + 1}')}</c:val>"
            "</c:ser>"
        )
    sers_xml = "".join(sers)
    if kind == "pie":
        plot = f'<c:pieChart><c:varyColors val="1"/>{sers_xml}<c:firstSliceAng val="0"/></c:pieChart>'
    else:
        axes = (
            '<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            '<c:delete val="0"/><c:axPos val="b"/><c:crossAx val="222222222"/></c:catAx>'
            '<c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            '<c:delete val="0"/><c:axPos val="l"/><c:crossAx val="111111111"/></c:valAx>'
        )
        if kind == "bar":
            inner = (
                f'<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="0"/>{sers_xml}'
                '<c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>'
            )
        elif kind == "area":
            inner = (
                f'<c:areaChart><c:grouping val="standard"/><c:varyColors val="0"/>{sers_xml}'
                '<c:axId val="111111111"/><c:axId val="222222222"/></c:areaChart>'
            )
        else:
            inner = (
                f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{sers_xml}<c:marker val="1"/>'
                '<c:axId val="111111111"/><c:axId val="222222222"/></c:lineChart>'
            )
        plot = inner + axes
    title = (
        (
            "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r>"
            f"<a:t>{escape_xml_text(chart['title'])}</a:t></a:r></a:p></c:rich></c:tx>"
            '<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>'
        )
        if chart.get("title")
        else ""
    )
    return (
        XML_DECL
        + '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<c:chart>{title}<c:plotArea><c:layout/>{plot}</c:plotArea>"
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>'
        + (
            f'<c:externalData r:id="{escape_xml_attr(external_data_r_id)}"><c:autoUpdate val="0"/></c:externalData>'
            if external_data_r_id
            else ""
        )
        + "</c:chartSpace>"
    )


def _zip_to_bytes(z: zipfile.ZipFile) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for name in z.namelist():
            out.writestr(name, z.read(name))
    return buf.getvalue()


def build_chart_workbook_xlsx(categories, series) -> str:
    categories = list(categories or [])
    series = list(series or [])
    rows = len(categories)
    if rows > MAX_XLSX_ROWS - 1:
        raise ValueError("too many chart categories for an XLSX worksheet")
    if len(series) > MAX_XLSX_COLS - 1:
        raise ValueError("too many chart series for an XLSX worksheet")
    strings = []

    def si(s):
        s = "" if s is None else str(s)
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    header = [f'<c r="A1" t="s"><v>{si("")}</v></c>']
    for j, ser in enumerate(series):
        header.append(
            f'<c r="{_xlsx_col(j + 1)}1" t="s"><v>{si(ser.get("name", f"Series {j + 1}"))}</v></c>'
        )
    data_rows = []
    for i in range(rows):
        rn = i + 2
        cells = [f'<c r="A{rn}" t="s"><v>{si(categories[i])}</v></c>']
        for j, ser in enumerate(series):
            values = ser.get("values") or []
            v = values[i] if i < len(values) else None
            text = _num_text(v)
            if text is not None:
                cells.append(
                    f'<c r="{_xlsx_col(j + 1)}{rn}"><v>{escape_xml_text(text)}</v></c>'
                )
        data_rows.append(f'<row r="{rn}">{"".join(cells)}</row>')
    sheet = (
        XML_DECL
        + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1">{"".join(header)}</row>{"".join(data_rows)}</sheetData></worksheet>'
    )
    sst = (
        XML_DECL
        + f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{escape_xml_text(s)}</t></si>" for s in strings)
        + "</sst>"
    )
    wb = (
        XML_DECL
        + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        XML_DECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    top_rels = (
        XML_DECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    ct = (
        XML_DECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", top_rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", sst)
    return base64.b64encode(buf.getvalue()).decode()


def patch_chart_part_xml(xml: str, patch: dict) -> str:
    """Патч кэшей чарта (title/categories/series) с сохранением структуры."""
    edits = []

    def tag_range(tag, frm=0, to=None):
        to = len(xml) if to is None else to
        open_prefix = "<" + tag
        i = frm
        while i < to:
            o = xml.find(open_prefix, i)
            if o == -1 or o >= to:
                return None
            after = xml[o + len(open_prefix) : o + len(open_prefix) + 1]
            if after not in ("", ">", " ", "/"):
                i = o + len(open_prefix)
                continue
            close = xml.find("</" + tag + ">", o)
            if close == -1 or close >= to:
                return None
            return (o, close + len(tag) + 3)
        return None

    def tag_ranges(tag):
        out, frm = [], 0
        while True:
            r = tag_range(tag, frm)
            if not r:
                return out
            out.append(r)
            frm = r[1]

    def inner_text_ranges(tag, frm, to):
        out = []
        for m in re.finditer(rf"<{tag}(?:\s[^>]*)?>([\s\S]*?)</{tag}>", xml):
            if m.start() < frm or m.start() >= to:
                continue
            open_end = m.start() + m.group(0).index(">") + 1
            out.append((open_end, open_end + len(m.group(1))))
        return out

    if patch.get("title") is not None:
        t = tag_range("c:title")
        if t:
            texts = inner_text_ranges("a:t", t[0], t[1])
            for i, rng in enumerate(texts):
                edits.append((rng[0], rng[1], patch["title"] if i == 0 else ""))
    for i, ser_rng in enumerate(tag_ranges("c:ser")):
        sp = (
            (patch.get("series") or [None] * (i + 1))[i]
            if patch.get("series") and i < len(patch["series"])
            else None
        )
        if sp and sp.get("name") is not None:
            tx = tag_range("c:tx", ser_rng[0], ser_rng[1])
            if tx:
                texts = inner_text_ranges("c:v", tx[0], tx[1])
                if texts:
                    edits.append((texts[0][0], texts[0][1], sp["name"]))
        if sp and sp.get("values"):
            val = tag_range("c:val", ser_rng[0], ser_rng[1])
            if val:
                _push_point_edits(
                    xml,
                    val,
                    [None if v is None else str(v) for v in sp["values"]],
                    edits,
                    inner_text_ranges,
                    "c:v",
                )
        if patch.get("categories"):
            cat = tag_range("c:cat", ser_rng[0], ser_rng[1])
            if cat:
                _push_point_edits(
                    xml, cat, patch["categories"], edits, inner_text_ranges, "c:v"
                )
    if not edits:
        return xml
    out = xml
    for start, end, text in sorted(edits, key=lambda e: -e[0]):
        out = out[:start] + escape_xml_text(text) + out[end:]
    return out


def _push_point_edits(xml, rng, texts, edits, inner_text_ranges, tag):
    for m in re.finditer(r'<c:pt idx="(\d+)"[^>]*>([\s\S]*?)</c:pt>', xml):
        if m.start() < rng[0] or m.start() >= rng[1]:
            continue
        idx = int(m.group(1))
        if idx >= len(texts) or texts[idx] is None:
            continue
        inner = inner_text_ranges(tag, m.start(), m.end())
        if inner:
            edits.append((inner[0][0], inner[0][1], texts[idx]))


def patch_chart_workbook_xlsx(b64: str, categories, series):
    try:
        src = zipfile.ZipFile(io.BytesIO(base64.b64decode(b64)))
        sheet = src.read("xl/worksheets/sheet1.xml").decode("utf-8")

        def inline(ref, text):
            return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape_xml_text(str(text))}</t></is></c>'

        header = [inline("A1", "")]
        for j, ser in enumerate(series):
            header.append(
                inline(f"{_xlsx_col(j + 1)}1", ser.get("name", f"Series {j + 1}"))
            )
        data_rows = []
        for i, cat in enumerate(categories):
            rn = i + 2
            cells = [inline(f"A{rn}", cat)]
            for j, ser in enumerate(series):
                values = ser.get("values") or []
                v = values[i] if i < len(values) else None
                text = _num_text(v)
                if text is not None:
                    cells.append(
                        f'<c r="{_xlsx_col(j + 1)}{rn}"><v>{escape_xml_text(text)}</v></c>'
                    )
            data_rows.append(f'<row r="{rn}">{"".join(cells)}</row>')
        new_sd = f'<sheetData><row r="1">{"".join(header)}</row>{"".join(data_rows)}</sheetData>'
        if not re.search(r"<sheetData/>|<sheetData[\s>]", sheet):
            return None
        updated = re.sub(
            r"<sheetData/>|<sheetData[^>]*>[\s\S]*?</sheetData>", new_sd, sheet, count=1
        )
        last_ref = f"{_xlsx_col(len(series))}{len(categories) + 1}"
        updated = re.sub(
            r"<dimension[^>]*/>", f'<dimension ref="A1:{last_ref}"/>', updated
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            for name in src.namelist():
                out.writestr(
                    name,
                    updated.encode()
                    if name == "xl/worksheets/sheet1.xml"
                    else src.read(name),
                )
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None
