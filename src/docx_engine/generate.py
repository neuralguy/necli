"""Генерация OOXML-фрагментов и хирургические патчи."""

from __future__ import annotations

import re

from .models import GeneratedBlock, ParaFormat, Run
from .xml_utils import escape_xml_attr, escape_xml_text, unescape_xml_text

EMU_PER_PX = 9525
EMU_PER_PT = 12700
TABLE_HEADER_FILL = "F2F2F2"

PPR_CHILD_ORDER = [
    "w:pStyle",
    "w:keepNext",
    "w:keepLines",
    "w:pageBreakBefore",
    "w:framePr",
    "w:widowControl",
    "w:numPr",
    "w:suppressLineNumbers",
    "w:pBdr",
    "w:shd",
    "w:tabs",
    "w:suppressAutoHyphens",
    "w:kinsoku",
    "w:wordWrap",
    "w:overflowPunct",
    "w:topLinePunct",
    "w:autoSpaceDE",
    "w:autoSpaceDN",
    "w:bidi",
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
]

RPR_CHILD_ORDER = [
    "w:rStyle",
    "w:rFonts",
    "w:b",
    "w:bCs",
    "w:i",
    "w:iCs",
    "w:caps",
    "w:smallCaps",
    "w:strike",
    "w:dstrike",
    "w:outline",
    "w:shadow",
    "w:emboss",
    "w:imprint",
    "w:noProof",
    "w:snapToGrid",
    "w:vanish",
    "w:webHidden",
    "w:color",
    "w:spacing",
    "w:w",
    "w:kern",
    "w:position",
    "w:sz",
    "w:szCs",
    "w:highlight",
    "w:u",
    "w:effect",
    "w:bdr",
    "w:shd",
    "w:fitText",
    "w:vertAlign",
    "w:rtl",
    "w:cs",
    "w:em",
    "w:lang",
    "w:eastAsianLayout",
    "w:specVanish",
    "w:oMath",
    "w:rPrChange",
]

FORMAT_MANAGED = {
    "w:keepNext",
    "w:keepLines",
    "w:pageBreakBefore",
    "w:pBdr",
    "w:shd",
    "w:bidi",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:jc",
}


# ---------- splitXmlChildren ----------
_TAG_RE = re.compile(r'<(/?)([A-Za-z0-9:._-]+)((?:"[^"]*"|\'[^\']*\'|[^"\'>])*)>')


def split_xml_children(xml: str) -> list[tuple[str, str]]:
    out, depth, start, name = [], 0, -1, ""
    for m in _TAG_RE.finditer(xml):
        closing, self_closing = m.group(1) == "/", m.group(3).endswith("/")
        if closing:
            depth -= 1
            if depth == 0:
                out.append((name, xml[start : m.end()]))
        elif self_closing:
            if depth == 0:
                out.append((m.group(2), m.group(0)))
        else:
            if depth == 0:
                start, name = m.start(), m.group(2)
            depth += 1
    return out


# ---------- форматные дети pPr ----------
def _format_children(fmt: ParaFormat | None):
    if not fmt:
        return []
    out = []
    if fmt.keep_next:
        out.append(("w:keepNext", "<w:keepNext/>"))
    if fmt.keep_lines:
        out.append(("w:keepLines", "<w:keepLines/>"))
    if fmt.page_break_before:
        out.append(("w:pageBreakBefore", "<w:pageBreakBefore/>"))
    if fmt.widow_control is not None:
        out.append(
            (
                "w:widowControl",
                "<w:widowControl/>" if fmt.widow_control else '<w:widowControl w:val="0"/>',
            )
        )
    if fmt.borders:

        def line(side):
            return f'<w:{side} w:val="single" w:sz="4" w:space="1" w:color="auto"/>'

        sides = []
        if "t" in fmt.borders:
            sides.append(line("top"))
        if "l" in fmt.borders:
            sides.append(line("left"))
        if "b" in fmt.borders:
            sides.append(line("bottom"))
        if "r" in fmt.borders:
            sides.append(line("right"))
        if sides:
            out.append(("w:pBdr", f"<w:pBdr>{''.join(sides)}</w:pBdr>"))
    if fmt.shading_fill:
        out.append(
            (
                "w:shd",
                f'<w:shd w:val="clear" w:color="auto" w:fill="{escape_xml_attr(fmt.shading_fill)}"/>',
            )
        )
    if fmt.bidi:
        out.append(("w:bidi", "<w:bidi/>"))
    if fmt.contextual_spacing:
        out.append(("w:contextualSpacing", "<w:contextualSpacing/>"))
    sp = []
    if fmt.space_before is not None and fmt.space_before >= 0:
        sp.append(f'w:before="{round(fmt.space_before)}"')
    if fmt.space_after is not None and fmt.space_after >= 0:
        sp.append(f'w:after="{round(fmt.space_after)}"')
    if fmt.line_rule in ("exact", "atLeast") and fmt.line_raw_twips:
        sp.append(f'w:line="{round(fmt.line_raw_twips)}"')
        sp.append(f'w:lineRule="{fmt.line_rule}"')
    elif fmt.line_rule == "auto" and fmt.line_raw_twips:
        sp.append(f'w:line="{round(fmt.line_raw_twips)}"')
        sp.append('w:lineRule="auto"')
    elif fmt.line_spacing and fmt.line_spacing > 0:
        sp.append(f'w:line="{round(fmt.line_spacing * 240)}"')
        sp.append('w:lineRule="auto"')
    if sp:
        out.append(("w:spacing", f"<w:spacing {' '.join(sp)}/>"))
    ind = []
    if fmt.indent_left is not None:
        ind.append(f'w:left="{round(fmt.indent_left)}"')
    if fmt.indent_right is not None:
        ind.append(f'w:right="{round(fmt.indent_right)}"')
    if fmt.indent_first_line is not None:
        if fmt.indent_first_line >= 0:
            ind.append(f'w:firstLine="{round(fmt.indent_first_line)}"')
        else:
            ind.append(f'w:hanging="{round(-fmt.indent_first_line)}"')
    if ind:
        out.append(("w:ind", f"<w:ind {' '.join(ind)}/>"))
    if fmt.align:
        jc = "both" if fmt.align == "justify" else fmt.align
        if fmt.bidi and jc in ("left", "right"):
            jc = "right" if jc == "left" else "left"
        out.append(("w:jc", f'<w:jc w:val="{escape_xml_attr(jc)}"/>'))
    if fmt.tab_stops:
        tabs = []
        for ts in fmt.tab_stops:
            pos = int(ts["pos"])
            x = f'<w:tab w:val="{escape_xml_attr(ts["val"])}" w:pos="{pos}"'
            if ts.get("leader") and ts["leader"] != "none":
                x += f' w:leader="{escape_xml_attr(ts["leader"])}"'
            tabs.append(x + "/>")
        out.append(("w:tabs", f"<w:tabs>{''.join(tabs)}</w:tabs>"))
    if fmt.drop_cap:
        out.append(
            (
                "w:framePr",
                (
                    f'<w:framePr w:dropCap="{escape_xml_attr(fmt.drop_cap["type"])}" w:lines="{int(fmt.drop_cap["lines"])}" '
                    'w:wrap="around" w:vAnchor="text" w:hAnchor="text"/>'
                ),
            )
        )
    return out


def merge_p_pr_format(raw_p_pr: str, fmt: ParaFormat | None) -> str:
    """Merge modeled paragraph formatting while preserving untouched OOXML.

    A parsed paragraph can contain details the public model does not represent
    (custom border colors, auto-spacing flags, compatibility attributes, etc.).
    Replacing every managed child on any edit would silently destroy those
    details, so a group is rebuilt only when its modeled semantic value differs.
    """
    if fmt is None:
        return raw_p_pr
    m = re.match(r"<w:pPr(?: [^>]*)?>", raw_p_pr)
    fresh = _format_children(fmt)
    fresh.sort(key=lambda c: PPR_CHILD_ORDER.index(c[0]) if c[0] in PPR_CHILD_ORDER else 99)
    if not m:
        return f"<w:pPr>{''.join(x for _, x in fresh)}</w:pPr>" if fresh else ""
    open_tag = m.group(0)
    inner = raw_p_pr[len(open_tag) : len(raw_p_pr) - len("</w:pPr>")]
    raw_children = split_xml_children(inner)

    def raw_of(tag):
        return next((x for n, x in raw_children if n == tag), None)

    fresh_by = {n: x for n, x in fresh}

    def attr(xml, name):
        if not xml:
            return None
        mm = re.search(rf'\s{re.escape(name)}="([^"]*)"', xml)
        return mm.group(1) if mm else None

    def int_attr(xml, *names):
        for name in names:
            v = attr(xml, name)
            if v is not None:
                try:
                    return int(v)
                except ValueError:
                    return None
        return None

    def bool_xml(xml):
        if not xml:
            return False
        v = attr(xml, "w:val")
        return v is None or v.lower() not in ("0", "false", "off", "none")

    def border_sides(xml):
        if not xml:
            return None
        out = ""
        for side, ch in (("top", "t"), ("bottom", "b"), ("left", "l"), ("right", "r")):
            mm = re.search(rf"<w:{side}\b[^>]*/?>", xml)
            if mm:
                v = attr(mm.group(0), "w:val")
                if v not in ("none", "nil"):
                    out += ch
        return out or None

    def tabs_model(xml):
        if not xml:
            return None
        out = []
        for mm in re.finditer(r"<w:tab\b[^>]*/>", xml):
            tag = mm.group(0)
            try:
                pos = int(attr(tag, "w:pos"))
            except (TypeError, ValueError):
                continue
            val = attr(tag, "w:val") or "left"
            item = {"pos": pos, "val": val}
            leader = attr(tag, "w:leader")
            if leader and leader != "none":
                item["leader"] = leader
            out.append(item)
        return out or None

    def group_equal(tag, raw):
        if tag == "w:keepNext":
            return bool_xml(raw) == bool(fmt.keep_next)
        if tag == "w:keepLines":
            return bool_xml(raw) == bool(fmt.keep_lines)
        if tag == "w:pageBreakBefore":
            return bool_xml(raw) == bool(fmt.page_break_before)
        if tag == "w:widowControl":
            return fmt.widow_control is None or bool_xml(raw) == bool(fmt.widow_control)
        if tag == "w:contextualSpacing":
            return bool_xml(raw) == bool(fmt.contextual_spacing)
        if tag == "w:bidi":
            return bool_xml(raw) == bool(fmt.bidi)
        if tag == "w:shd":
            fill = attr(raw, "w:fill")
            return (None if fill in (None, "auto") else fill) == fmt.shading_fill
        if tag == "w:pBdr":
            return border_sides(raw) == (fmt.borders or None)
        if tag == "w:spacing":
            before = int_attr(raw, "w:before")
            after = int_attr(raw, "w:after")
            line = int_attr(raw, "w:line")
            rule = attr(raw, "w:lineRule") or ("auto" if line is not None else None)
            if before != fmt.space_before or after != fmt.space_after:
                return False
            if line is None:
                return fmt.line_raw_twips is None and fmt.line_spacing is None
            if fmt.line_raw_twips is not None and line != round(fmt.line_raw_twips):
                return False
            if (
                fmt.line_raw_twips is None
                and fmt.line_spacing is not None
                and line != round(fmt.line_spacing * 240)
            ):
                return False
            return (fmt.line_rule or "auto") == rule
        if tag == "w:ind":
            left = int_attr(raw, "w:left", "w:start")
            right = int_attr(raw, "w:right", "w:end")
            first = int_attr(raw, "w:firstLine")
            hanging = int_attr(raw, "w:hanging")
            modeled_first = fmt.indent_first_line
            raw_first = first if first is not None else (-hanging if hanging is not None else None)
            return (
                left == fmt.indent_left and right == fmt.indent_right and raw_first == modeled_first
            )
        if tag == "w:jc":
            raw_jc = attr(raw, "w:val")
            modeled = fmt.align
            if modeled == "justify":
                modeled = "both"
            if fmt.bidi and modeled in ("left", "right"):
                modeled = "right" if modeled == "left" else "left"
            # start/end are equivalent to left/right in the public model.
            if raw_jc == "start":
                raw_jc = "left"
            if raw_jc == "end":
                raw_jc = "right"
            return raw_jc == modeled
        if tag == "w:tabs":
            return fmt.tab_stops is None or tabs_model(raw) == fmt.tab_stops
        if tag == "w:framePr":
            if fmt.drop_cap is None:
                return True
            try:
                lines = int(attr(raw, "w:lines") or 3)
            except ValueError:
                lines = 3
            return attr(raw, "w:dropCap") == fmt.drop_cap.get("type") and lines == int(
                fmt.drop_cap.get("lines", 3)
            )
        return True

    managed = set(FORMAT_MANAGED)
    if fmt.widow_control is not None:
        managed.add("w:widowControl")
    if fmt.tab_stops is not None:
        managed.add("w:tabs")
    if fmt.drop_cap is not None:
        managed.add("w:framePr")

    changed = set()
    for tag in managed:
        raw = raw_of(tag)
        if (raw is not None and not group_equal(tag, raw)) or (raw is None and tag in fresh_by):
            changed.add(tag)

    kept = [(n, x) for n, x in raw_children if n not in changed]
    replacements = [(n, x) for n, x in fresh if n in changed]
    combined = kept + replacements
    if not combined:
        return ""
    combined.sort(key=lambda c: PPR_CHILD_ORDER.index(c[0]) if c[0] in PPR_CHILD_ORDER else 99)
    return f"{open_tag}{''.join(x for _, x in combined)}</w:pPr>"


def strip_p_pr_change(raw_p_pr: str) -> str:
    start = raw_p_pr.find("<w:pPrChange")
    if start == -1:
        return raw_p_pr
    end = raw_p_pr.find("</w:pPrChange>", start)
    if end == -1:
        return raw_p_pr
    return raw_p_pr[:start] + raw_p_pr[end + len("</w:pPrChange>") :]


# ---------- rPr ----------
def _model_rpr_children(run: Run, inside_link: bool):
    out = []
    if inside_link:
        out.append(("w:rStyle", '<w:rStyle w:val="Hyperlink"/>'))
    elif run.style_id:
        out.append(("w:rStyle", f'<w:rStyle w:val="{escape_xml_attr(run.style_id)}"/>'))
    if run.font:
        f = escape_xml_attr(run.font)
        out.append(
            ("w:rFonts", f'<w:rFonts w:ascii="{f}" w:eastAsia="{f}" w:hAnsi="{f}" w:cs="{f}"/>')
        )
    if run.bold:
        out.append(("w:b", "<w:b/>"))
    if run.italic:
        out.append(("w:i", "<w:i/>"))
    if run.strike:
        out.append(("w:strike", "<w:strike/>"))
    if run.color:
        out.append(("w:color", f'<w:color w:val="{escape_xml_attr(run.color)}"/>'))
    if run.char_spacing_twips is not None:
        out.append(("w:spacing", f'<w:spacing w:val="{int(run.char_spacing_twips)}"/>'))
    if run.char_scale_pct is not None:
        scale = max(1, min(600, int(run.char_scale_pct)))
        out.append(("w:w", f'<w:w w:val="{scale}"/>'))
    if run.size_half_points is not None:
        try:
            size = int(run.size_half_points)
        except (TypeError, ValueError) as exc:
            raise ValueError("run size_half_points must be a positive integer") from exc
        if size <= 0:
            raise ValueError("run size_half_points must be a positive integer")
        out.append(("w:sz", f'<w:sz w:val="{size}"/>'))
        out.append(("w:szCs", f'<w:szCs w:val="{size}"/>'))
    if run.highlight:
        out.append(("w:highlight", f'<w:highlight w:val="{escape_xml_attr(run.highlight)}"/>'))
    if run.underline:
        out.append(("w:u", '<w:u w:val="single"/>'))
    if run.vert_align and run.vert_align in ("superscript", "subscript"):
        out.append(("w:vertAlign", f'<w:vertAlign w:val="{run.vert_align}"/>'))
    if run.em:
        out.append(("w:em", f'<w:em w:val="{escape_xml_attr(run.em)}"/>'))
    return out


def _raw_attr(xml, attr):
    if not xml:
        return None
    m = re.search(rf' {re.escape(attr)}="([^"]*)"', xml)
    return m.group(1) if m else None


def _raw_bool(xml):
    if not xml:
        return False
    v = _raw_attr(xml, "w:val")
    if v is None:
        return True
    return v.lower() not in ("0", "false", "none", "off")


def merge_r_pr_model(raw_r_pr: str, run: Run, inside_link: bool) -> str:
    m = re.match(r"<w:rPr(?: [^>]*)?>", raw_r_pr)
    fresh = _model_rpr_children(run, inside_link)
    if not m:
        return f"<w:rPr>{''.join(x for _, x in fresh)}</w:rPr>" if fresh else ""
    open_tag = m.group(0)
    inner = raw_r_pr[len(open_tag) : len(raw_r_pr) - len("</w:rPr>")]
    raw_children = split_xml_children(inner)

    def raw_of(tag):
        return next((x for n, x in raw_children if n == tag), None)

    def group_equal(key):
        if key == "rStyle":
            raw = _raw_attr(raw_of("w:rStyle"), "w:val")
            modeled = "Hyperlink" if inside_link else run.style_id
            return raw == modeled or (raw == "Hyperlink" and not modeled)
        if key == "rFonts":
            a = raw_of("w:rFonts")
            raw = _raw_attr(a, "w:eastAsia") or _raw_attr(a, "w:ascii") or _raw_attr(a, "w:hAnsi")
            return raw == run.font
        if key == "bold":
            return _raw_bool(raw_of("w:b")) == bool(run.bold)
        if key == "italic":
            return _raw_bool(raw_of("w:i")) == bool(run.italic)
        if key == "strike":
            return _raw_bool(raw_of("w:strike")) == bool(run.strike)
        if key == "color":
            raw = _raw_attr(raw_of("w:color"), "w:val")
            return (None if raw == "auto" else raw) == run.color
        if key == "size":
            raw = _raw_attr(raw_of("w:sz"), "w:val")
            rv = int(raw) if raw else None
            return rv == run.size_half_points
        if key == "highlight":
            raw = _raw_attr(raw_of("w:highlight"), "w:val")
            return (None if raw == "none" else raw) == run.highlight
        if key == "underline":
            val = _raw_attr(raw_of("w:u"), "w:val")
            raw_on = raw_of("w:u") is not None and (
                val is None or val.lower() not in ("none", "0", "false", "off")
            )
            return raw_on == bool(run.underline)
        if key == "spacing":
            raw = _raw_attr(raw_of("w:spacing"), "w:val")
            try:
                rv = int(raw) if raw is not None else None
            except ValueError:
                rv = None
            return rv == run.char_spacing_twips
        if key == "scale":
            raw = _raw_attr(raw_of("w:w"), "w:val")
            try:
                rv = int(raw) if raw is not None else None
            except ValueError:
                rv = None
            return rv == run.char_scale_pct
        if key == "vertAlign":
            raw = _raw_attr(raw_of("w:vertAlign"), "w:val")
            modeled = raw if raw in ("superscript", "subscript") else None
            return modeled == run.vert_align
        if key == "em":
            return _raw_attr(raw_of("w:em"), "w:val") == run.em
        return True

    groups = [
        ("rStyle", ["w:rStyle"]),
        ("rFonts", ["w:rFonts"]),
        ("bold", ["w:b", "w:bCs"]),
        ("italic", ["w:i", "w:iCs"]),
        ("strike", ["w:strike"]),
        ("color", ["w:color"]),
        ("spacing", ["w:spacing"]),
        ("scale", ["w:w"]),
        ("size", ["w:sz", "w:szCs"]),
        ("highlight", ["w:highlight"]),
        ("underline", ["w:u"]),
        ("vertAlign", ["w:vertAlign"]),
        ("em", ["w:em"]),
    ]
    rebuilt, fresh_out = set(), []
    fresh_by = {}
    for f in fresh:
        g = next(grp for grp, tags in groups if f[0] in tags)
        fresh_by.setdefault(g, []).append(f)
    for g, tags in groups:
        if group_equal(g):
            continue
        rebuilt.update(tags)
        fresh_out.extend(fresh_by.get(g, []))
    kept = [(n, x) for n, x in raw_children if n not in rebuilt]

    def rank(n):
        return RPR_CHILD_ORDER.index(n) if n in RPR_CHILD_ORDER else -1

    parts, fi, prev = [], 0, -1
    for n, x in kept:
        own = rank(n)
        effective = prev if own == -1 else max(own, prev)
        while fi < len(fresh_out) and rank(fresh_out[fi][0]) < effective:
            parts.append(fresh_out[fi][1])
            fi += 1
        parts.append(x)
        prev = effective
    while fi < len(fresh_out):
        parts.append(fresh_out[fi][1])
        fi += 1
    if not parts:
        return ""
    return f"{open_tag}{''.join(parts)}</w:rPr>"


# ---------- закладки / комментарии ----------
def _bookmark_id(name: str) -> int:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    return abs(h) % 0x7FFFFFFF


def _bookmarks_xml(names):
    if not names:
        return ""
    out = []
    for n in names:
        i = _bookmark_id(n)
        out.append(
            f'<w:bookmarkStart w:id="{i}" w:name="{escape_xml_attr(n)}"/><w:bookmarkEnd w:id="{i}"/>'
        )
    return "".join(out)


# ---------- runs ----------
def _run_fragment_xml(run: Run, inside_link: bool) -> str:
    if run.math:
        return run.math["omml"]
    if run.ruby:
        return f"<w:r>{run.ruby['xml']}</w:r>"
    if run.note_ref:
        tag = "w:footnoteReference" if run.note_ref["kind"] == "footnote" else "w:endnoteReference"
        return (
            '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
            f'<{tag} w:id="{escape_xml_attr(run.note_ref["id"])}"/></w:r>'
        )
    if run.ref_field is not None:
        name = run.ref_field.replace('"', "")
        clone = Run(
            text=run.text,
            raw_r_pr=run.raw_r_pr,
            style_id=run.style_id,
            bold=run.bold,
            italic=run.italic,
            underline=run.underline,
            strike=run.strike,
            color=run.color,
            size_half_points=run.size_half_points,
            font=run.font,
            char_spacing_twips=run.char_spacing_twips,
            char_scale_pct=run.char_scale_pct,
            highlight=run.highlight,
            vert_align=run.vert_align,
            em=run.em,
        )
        return (
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> REF {escape_xml_text(name)} \\h </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            + _generate_run_xml(clone, inside_link)
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        )
    if run.instr_field is not None:
        clone = Run(
            text=run.text,
            raw_r_pr=run.raw_r_pr,
            style_id=run.style_id,
            bold=run.bold,
            italic=run.italic,
            underline=run.underline,
            strike=run.strike,
            color=run.color,
            size_half_points=run.size_half_points,
            font=run.font,
            char_spacing_twips=run.char_spacing_twips,
            char_scale_pct=run.char_scale_pct,
            highlight=run.highlight,
            vert_align=run.vert_align,
            em=run.em,
        )
        return (
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> {escape_xml_text(run.instr_field)} </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            + _generate_run_xml(clone, inside_link)
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        )
    xml = "" if run.text == "" else _generate_run_xml(run, inside_link)
    if run.xe_term is not None:
        term = run.xe_term.replace('"', "")
        xml += (
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText xml:space="preserve"> XE "{escape_xml_text(term)}" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        )
    return xml


def _generate_run_xml(run: Run, inside_link: bool) -> str:
    if run.raw_r_pr is not None:
        r_pr = merge_r_pr_model(run.raw_r_pr, run, inside_link)
    else:
        props = [x for _, x in _model_rpr_children(run, inside_link)]
        r_pr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    text_tag = "w:delText" if run.del_ else "w:t"
    segments, buf = [], []

    def flush():
        if buf:
            segments.append(
                f'<{text_tag} xml:space="preserve">{escape_xml_text("".join(buf))}</{text_tag}>'
            )
            buf.clear()

    for ch in run.text:
        if ch == "\t":
            flush()
            segments.append("<w:tab/>")
        elif ch == "\n":
            flush()
            segments.append("<w:br/>")
        elif ch == "\f":
            flush()
            segments.append('<w:br w:type="page"/>')
        else:
            buf.append(ch)
    flush()
    return f"<w:r>{r_pr}{''.join(segments)}</w:r>"


def _rev_key(r: Run):
    if r.ins or r.del_:
        import json

        return json.dumps(
            [
                r.ins.author if r.ins else None,
                r.ins.date if r.ins else None,
                r.ins.id if r.ins else None,
                r.del_.author if r.del_ else None,
                r.del_.date if r.del_ else None,
                r.del_.id if r.del_ else None,
            ]
        )
    return ""


def _rev_attrs(info):
    seq = getattr(_rev_attrs, "seq", 9001)
    _rev_attrs.seq = seq + 1
    return (
        f' w:id="{escape_xml_attr(info.id or str(seq))}"'
        f' w:author="{escape_xml_attr(info.author)}"'
        + (f' w:date="{escape_xml_attr(info.date)}"' if info.date else "")
    )


def _runs_xml(runs: list[Run], allocate) -> str:
    first_of, last_of = {}, {}
    for i, run in enumerate(runs):
        for cid in run.comment_ids or []:
            first_of.setdefault(cid, i)
            last_of[cid] = i

    def starts_at(i):
        return "".join(
            f'<w:commentRangeStart w:id="{escape_xml_attr(cid)}"/>'
            for cid, at in first_of.items()
            if at == i
        )

    def ends_at(i):
        return "".join(
            f'<w:commentRangeEnd w:id="{escape_xml_attr(cid)}"/>'
            f'<w:r><w:commentReference w:id="{escape_xml_attr(cid)}"/></w:r>'
            for cid, at in last_of.items()
            if at == i
        )

    parts = []

    def emit_range(frm, to):
        i = frm
        while i < to:
            run = runs[i]
            if run.link:
                group, group_start, href = [], i, run.link["href"]
                tooltip = run.link.get("tooltip")
                r_id = run.link.get("rId")
                while (
                    i < to
                    and runs[i].link
                    and runs[i].link["href"] == href
                    and runs[i].link.get("tooltip") == tooltip
                ):
                    r_id = r_id or runs[i].link.get("rId")
                    group.append(runs[i])
                    i += 1
                for j in range(group_start, i):
                    parts.append(starts_at(j))
                tip = tooltip
                tip_attr = f' w:tooltip="{escape_xml_attr(tip)}"' if tip else ""
                if href.startswith("#"):
                    inner = "".join(_run_fragment_xml(r, True) for r in group)
                    parts.append(
                        f'<w:hyperlink w:anchor="{escape_xml_attr(href[1:])}"{tip_attr}>{inner}</w:hyperlink>'
                    )
                else:
                    final = r_id or (allocate(href) if allocate else None)
                    if final:
                        inner = "".join(_run_fragment_xml(r, True) for r in group)
                        parts.append(
                            f'<w:hyperlink r:id="{escape_xml_attr(final)}"{tip_attr}>{inner}</w:hyperlink>'
                        )
                    else:
                        parts.append("".join(_run_fragment_xml(r, False) for r in group))
                for j in range(group_start, i):
                    parts.append(ends_at(j))
            else:
                parts.append(starts_at(i))
                parts.append(_run_fragment_xml(run, False))
                parts.append(ends_at(i))
                i += 1

    g = 0
    while g < len(runs):
        key = _rev_key(runs[g])
        end = g
        while end < len(runs) and _rev_key(runs[end]) == key:
            end += 1
        if key == "":
            emit_range(g, end)
        else:
            ins, dl = runs[g].ins, runs[g].del_
            if ins:
                parts.append(f"<w:ins{_rev_attrs(ins)}>")
            if dl:
                parts.append(f"<w:del{_rev_attrs(dl)}>")
            emit_range(g, end)
            if dl:
                parts.append("</w:del>")
            if ins:
                parts.append("</w:ins>")
        g = end
    return "".join(parts)


def inline_runs_xml(runs: list[Run]) -> str:
    # External hyperlinks require an OPC relationship id.  Silently degrading
    # them to plain text is data loss, so the context-free helper accepts only
    # links that already carry an rId (or internal #anchors).
    missing = [
        r.link.get("href")
        for r in runs
        if r.link and not str(r.link.get("href", "")).startswith("#") and not r.link.get("rId")
    ]
    if missing:
        raise ValueError("external hyperlink requires link.rId in inline_runs_xml")
    return _runs_xml(runs, None)


# ---------- абзац ----------
def generate_paragraph_xml(block: GeneratedBlock, ctx: dict | None = None) -> str:
    if block.type not in {"paragraph", "heading", "listItem"}:
        raise ValueError(f"unsupported paragraph block type: {block.type}")
    ctx = ctx or {}
    cross_starts = "".join(
        f'<w:commentRangeStart w:id="{escape_xml_attr(i)}"/>' for i in block.comment_starts or []
    )
    cross_ends = "".join(
        f'<w:commentRangeEnd w:id="{escape_xml_attr(i)}"/>'
        f'<w:r><w:commentReference w:id="{escape_xml_attr(i)}"/></w:r>'
        for i in block.comment_ends or []
    )
    content = (
        _bookmarks_xml(block.hidden_bookmarks)
        + _bookmarks_xml(block.bookmarks)
        + cross_starts
        + _runs_xml(block.runs, ctx.get("allocateHyperlinkRel"))
        + cross_ends
    )
    children = []
    style_id = None
    if block.type == "heading":
        level = min(max(block.level or 1, 1), 9)
        style_id = block.style_id or (ctx.get("headingStyleIds") or {}).get(level)
    elif block.type == "listItem":
        style_id = block.style_id or ctx.get("listParagraphStyleId")
    else:
        style_id = block.style_id
    if style_id:
        children.append(("w:pStyle", f'<w:pStyle w:val="{escape_xml_attr(style_id)}"/>'))
    if block.type == "listItem" and block.list:
        ilvl = min(max(int(block.list.get("ilvl", 0)), 0), 8)
        children.append(
            (
                "w:numPr",
                (
                    f'<w:numPr><w:ilvl w:val="{ilvl}"/>'
                    f'<w:numId w:val="{escape_xml_attr(str(block.list["numId"]))}"/></w:numPr>'
                ),
            )
        )
    children.extend(_format_children(block.format))
    children.sort(key=lambda c: PPR_CHILD_ORDER.index(c[0]) if c[0] in PPR_CHILD_ORDER else 99)
    if block.raw_p_pr is not None:
        # Preserve unknown pPr children from the original document while
        # applying the modeled paragraph format. Style/list are replaced only
        # when the generated model explicitly provides them.
        p_pr = merge_p_pr_format(block.raw_p_pr, block.format)
        if not p_pr and (style_id or (block.type == "listItem" and block.list)):
            p_pr = "<w:pPr></w:pPr>"

        def replace_child(raw, tag, replacement):
            if not raw:
                return raw
            m = re.match(r"<w:pPr(?: [^>]*)?>", raw)
            if not m:
                return raw
            open_tag = m.group(0)
            inner = raw[len(open_tag) : -len("</w:pPr>")]
            parts = split_xml_children(inner)
            parts = [(n, x) for n, x in parts if n != tag]
            if replacement:
                parts.append((tag, replacement))
            parts.sort(key=lambda c: PPR_CHILD_ORDER.index(c[0]) if c[0] in PPR_CHILD_ORDER else 99)
            return f"{open_tag}{''.join(x for _, x in parts)}</w:pPr>" if parts else ""

        if style_id:
            p_pr = replace_child(
                p_pr or "<w:pPr></w:pPr>",
                "w:pStyle",
                f'<w:pStyle w:val="{escape_xml_attr(style_id)}"/>',
            )
        if block.type == "listItem" and block.list:
            ilvl = min(max(int(block.list.get("ilvl", 0)), 0), 8)
            num_pr = (
                f'<w:numPr><w:ilvl w:val="{ilvl}"/>'
                f'<w:numId w:val="{escape_xml_attr(str(block.list["numId"]))}"/></w:numPr>'
            )
            p_pr = replace_child(p_pr or "<w:pPr></w:pPr>", "w:numPr", num_pr)
    else:
        p_pr = f"<w:pPr>{''.join(x for _, x in children)}</w:pPr>" if children else ""
    return f"<w:p>{p_pr}{content}</w:p>"


# ---------- таблицы ----------
def generate_table_xml(rows: int, cols: int, header_row: bool = False) -> str:
    try:
        rows = int(rows)
        cols = int(cols)
    except (TypeError, ValueError) as exc:
        raise ValueError("table rows/cols must be integers") from exc
    rows = min(max(rows, 1), 50)
    cols = min(max(cols, 1), 20)
    total = 9360
    cw = total // cols

    def border(n):
        return f'<w:{n} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'

    borders = (
        "<w:tblBorders>"
        + "".join(border(n) for n in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders>"
    )
    grid = "<w:tblGrid>" + f'<w:gridCol w:w="{cw}"/>' * cols + "</w:tblGrid>"

    def cell(header):
        shd = (
            f'<w:shd w:val="clear" w:color="auto" w:fill="{TABLE_HEADER_FILL}"/>' if header else ""
        )
        body = "<w:p><w:r><w:rPr><w:b/></w:rPr></w:r></w:p>" if header else "<w:p/>"
        return f'<w:tc><w:tcPr><w:tcW w:w="{cw}" w:type="dxa"/>{shd}</w:tcPr>{body}</w:tc>'

    def row(header):
        return f"<w:tr>{cell(header) * cols}</w:tr>"

    body = (row(True) + row(False) * max(rows - 1, 0)) if header_row else row(False) * rows
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>{borders}'
        '<w:tblLayout w:type="fixed"/></w:tblPr>' + grid + body + "</w:tbl><w:p/>"
    )


def generate_table_model_xml(model: dict, original_table_xml=None) -> str:
    rows = model.get("rows") or []

    def span_of(cell):
        try:
            return max(1, int(cell.get("colSpan") or 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("table colSpan must be a positive integer") from exc

    col_count = max(
        [1, len(model.get("colWidthsPct") or [])] + [sum(span_of(c) for c in row) for row in rows]
    )
    try:
        pcts = (
            [float(v) for v in model.get("colWidthsPct")]
            if model.get("colWidthsPct") and len(model["colWidthsPct"]) == col_count
            else [100 / col_count] * col_count
        )
        total_pct = sum(pcts) or 100
        widths = (
            [max(1, round(float(v))) for v in model["colWidthsTwips"]]
            if model.get("colWidthsTwips") and len(model["colWidthsTwips"]) == col_count
            else [max(1, round(v / total_pct * 9360)) for v in pcts]
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("table column widths must be numeric") from exc
    if any(v < 0 for v in pcts) or total_pct <= 0:
        raise ValueError("table percentage widths must be non-negative with positive total")
    total_width = sum(widths)

    def border(n):
        return f'<w:{n} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'

    borders = (
        "<w:tblBorders>"
        + "".join(border(n) for n in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders>"
    )
    grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"
    rows_xml = []
    for _ri, row in enumerate(rows):
        gc, cells = 0, []
        for cell in row:
            span = span_of(cell)
            width = sum(widths[gc : gc + span])
            gc += span
            normalized_cell = dict(cell)
            normalized_cell["colSpan"] = span
            cells.append(_table_cell_xml(normalized_cell, width))
        rows_xml.append(f"<w:tr>{''.join(cells)}</w:tr>")
    orig_tbl_pr = None
    if original_table_xml:
        m = re.search(
            r"<w:tblPr(?:\s[^>]*)?>[\s\S]*?</w:tblPr>|<w:tblPr(?:\s[^>]*)?/>", original_table_xml
        )
        orig_tbl_pr = m.group(0) if m else None
    tbl_pr = orig_tbl_pr or (
        f'<w:tblPr><w:tblW w:w="{total_width}" w:type="dxa"/>{borders}'
        '<w:tblLayout w:type="fixed"/></w:tblPr>'
    )
    return f"<w:tbl>{tbl_pr}{grid}{''.join(rows_xml)}</w:tbl>"


def _table_cell_xml(cell: dict, width: int) -> str:
    tc_pr = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
    if cell.get("colSpan") and cell["colSpan"] > 1:
        tc_pr.append(f'<w:gridSpan w:val="{cell["colSpan"]}"/>')
    if cell.get("vMerge") == "restart":
        tc_pr.append('<w:vMerge w:val="restart"/>')
    elif cell.get("vMerge") == "continue":
        tc_pr.append("<w:vMerge/>")
    if cell.get("fill"):
        tc_pr.append(
            f'<w:shd w:val="clear" w:color="auto" w:fill="{escape_xml_attr(cell["fill"])}"/>'
        )
    if cell.get("vAlign") and cell["vAlign"] != "top":
        tc_pr.append(f'<w:vAlign w:val="{escape_xml_attr(cell["vAlign"])}"/>')
    paras = cell.get("paras") or [""]
    content = []
    for text in paras:
        align = cell.get("align")
        jc_val = "both" if align == "justify" else align
        jc = f'<w:jc w:val="{escape_xml_attr(jc_val)}"/>' if jc_val else ""
        p_pr = f"<w:pPr>{jc}</w:pPr>" if jc else ""
        if text == "":
            content.append(f"<w:p>{p_pr}</w:p>")
        else:
            run = Run(text=text, bold=bool(cell.get("bold")), color=cell.get("color"))
            content.append(f"<w:p>{p_pr}{_runs_xml([run], None)}</w:p>")
    return f"<w:tc><w:tcPr>{''.join(tc_pr)}</w:tcPr>{''.join(content)}</w:tc>"


# ---------- патчи полей/формул/ячеек ----------
def _decode(s):
    return unescape_xml_text(s)


def _text_nodes(xml, tag):
    nodes = []
    for m in re.finditer(rf"<{tag}(?:\s[^>]*)?>([\s\S]*?)</{tag}>", xml):
        open_end = m.group(0).index(">") + 1
        close_start = m.group(0).rfind(f"</{tag}>")
        nodes.append(
            {
                "start": m.start(),
                "end": m.end(),
                "open": m.group(0)[:open_end],
                "close": m.group(0)[close_start:],
                "text": _decode(m.group(1)),
            }
        )
    return nodes


def _distribute(value, nodes):
    if not nodes:
        return []
    parts, cursor = [], 0
    for i, n in enumerate(nodes):
        if i == len(nodes) - 1:
            parts.append(value[cursor:])
            break
        parts.append(value[cursor : cursor + len(n["text"])])
        cursor += len(n["text"])
    return parts


def _replace_text_nodes(xml, replacements):
    out = xml
    for node, text in sorted(replacements, key=lambda r: -r[0]["start"]):
        rep = node["open"] + escape_xml_text(text) + node["close"]
        out = out[: node["start"]] + rep + out[node["end"] :]
    return out


def patch_field_paragraph_xml(xml: str, patch: dict) -> str:
    nodes = _text_nodes(xml, "w:t")
    if not nodes:
        return xml
    tab = re.search(r"<w:tab(?:\s[^>]*)?/>", xml)
    tab_index = tab.start() if tab else -1
    if patch.get("right") is not None and tab_index == -1:
        return xml
    left = nodes if tab_index == -1 else [n for n in nodes if n["start"] < tab_index]
    right = [] if tab_index == -1 else [n for n in nodes if n["start"] > tab_index]
    if patch.get("left") is not None and not left:
        return xml
    if patch.get("right") is not None and not right:
        return xml
    reps = []
    if patch.get("left") is not None:
        reps.extend(zip(left, _distribute(patch["left"], left), strict=False))
    if patch.get("right") is not None:
        reps.extend(zip(right, _distribute(patch["right"], right), strict=False))
    return _replace_text_nodes(xml, [(n, t) for n, t in reps])


def patch_math_tokens(xml: str, tokens: list[str]) -> str:
    nodes = _text_nodes(xml, "m:t")
    if not nodes or len(nodes) != len(tokens):
        return xml
    return _replace_text_nodes(xml, [(n, tokens[i]) for i, n in enumerate(nodes)])


# ---------- xmlSegments / патч ячеек ----------
def xml_segments(xml: str, tag: str, frm: int, to: int):
    open_prefix = "<" + tag
    close_tag = f"</{tag}>"
    segs, depth, seg_start = [], 0, -1
    i = frm
    while i < to:
        o = xml.find(open_prefix, i)
        c = xml.find(close_tag, i)
        if c == -1 or c >= to:
            break
        if o != -1 and o < c:
            after = xml[o + len(open_prefix) : o + len(open_prefix) + 1]
            if after not in ("", ">", " ", "/"):
                i = o + len(open_prefix)
                continue
            gt = xml.find(">", o)
            if gt != -1 and xml[gt - 1] == "/":
                if depth == 0:
                    segs.append((o, gt + 1))
                i = gt + 1
                continue
            if depth == 0:
                seg_start = o
            depth += 1
            i = o + len(open_prefix)
        else:
            depth -= 1
            if depth == 0:
                segs.append((seg_start, c + len(close_tag)))
            if depth < 0:
                break
            i = c + len(close_tag)
    return segs


def _patch_cell_xml(tc_xml: str, paras: list[str]):
    if tc_xml.find("<w:tbl", 1) != -1:
        return None
    m = re.match(r"<w:tc(?: [^>]*)?>", tc_xml)
    if not m:
        return None
    open_tag = m.group(0)
    tc_pr = re.search(r"<w:tcPr[\s\S]*?</w:tcPr>|<w:tcPr[^>]*/>", tc_xml)
    tc_pr = tc_pr.group(0) if tc_pr else ""
    first_p = re.search(r"<w:p(?: [^>]*)?>[\s\S]*?</w:p>", tc_xml)
    first_p = first_p.group(0) if first_p else ""
    p_pr = re.search(r"<w:pPr[\s\S]*?</w:pPr>|<w:pPr[^>]*/>", first_p)
    p_pr = p_pr.group(0) if p_pr else ""
    first_run = re.search(r"<w:r(?: [^>]*)?>[\s\S]*?</w:r>", first_p)
    first_run = first_run.group(0) if first_run else ""
    r_pr = re.search(r"<w:rPr[\s\S]*?</w:rPr>", first_run)
    r_pr = r_pr.group(0) if r_pr else ""
    body = []
    for t in paras:
        if t == "":
            body.append(f"<w:p>{p_pr}</w:p>")
        else:
            body.append(
                f'<w:p>{p_pr}<w:r>{r_pr}<w:t xml:space="preserve">{escape_xml_text(t)}</w:t></w:r></w:p>'
            )
    return open_tag + tc_pr + "".join(body) + "</w:tc>"


def patch_table_cell_texts(table_xml: str, texts) -> str:
    tr_segs = xml_segments(table_xml, "w:tr", 0, len(table_xml))
    out, cursor = "", 0
    for r, tr in enumerate(tr_segs):
        if r >= len(texts) or not texts[r]:
            continue
        tc_segs = xml_segments(table_xml, "w:tc", tr[0], tr[1])
        for c, tc in enumerate(tc_segs):
            if c >= len(texts[r]) or texts[r][c] is None:
                continue
            entry = texts[r][c]
            if not isinstance(entry, list):
                continue
            patched = _patch_cell_xml(table_xml[tc[0] : tc[1]], entry)
            if patched is None:
                continue
            out += table_xml[cursor : tc[0]] + patched
            cursor = tc[1]
    return out + table_xml[cursor:]


# ---------- изображения ----------
def patch_image_paragraph_xml(xml: str, patch: dict) -> str:
    out = xml
    if patch.get("widthPx") and patch.get("heightPx"):
        cx = max(1, round(patch["widthPx"] * EMU_PER_PX))
        cy = max(1, round(patch["heightPx"] * EMU_PER_PX))

        def resize(m):
            return re.sub(r'cx="\d+"', f'cx="{cx}"', re.sub(r'cy="\d+"', f'cy="{cy}"', m.group(0)))

        out = re.sub(r"<wp:extent[^>]*/?>", resize, out, count=1)
        out = re.sub(r"<a:ext[^>]*/>", resize, out, count=1)
    if "align" in patch:
        out = re.sub(r'<w:jc w:val="[^"]*"/>', "", out)
        out = out.replace("<w:pPr/>", "<w:pPr></w:pPr>")
        if patch["align"] not in (None, "left", "center", "right"):
            raise ValueError("image align must be left, center, or right")
        align = None if patch["align"] == "left" else patch["align"]
        if align:
            jc = f'<w:jc w:val="{escape_xml_attr(align)}"/>'
            m = re.search(r"(<w:pPr[^>]*>)([\s\S]*?)</w:pPr>", out)
            if m:
                inner = m.group(2)
                rpr_idx = inner.find("<w:rPr>")
                patched = inner + jc if rpr_idx == -1 else inner[:rpr_idx] + jc + inner[rpr_idx:]
                out = out[: m.start()] + m.group(1) + patched + "</w:pPr>" + out[m.end() :]
            else:
                out = re.sub(r"(<w:p(?: [^>]*)?>)", r"\1<w:pPr>" + jc + "</w:pPr>", out, count=1)
    if patch.get("posOffsetX") is not None:
        out = re.sub(
            r"(<wp:positionH[^>]*>[\s\S]*?)<wp:posOffset>-?\d+</wp:posOffset>([\s\S]*?</wp:positionH>)",
            lambda m: (
                m.group(1)
                + f"<wp:posOffset>{round(patch['posOffsetX'])}</wp:posOffset>"
                + m.group(2)
            ),
            out,
            count=1,
        )
    if patch.get("posOffsetY") is not None:
        out = re.sub(
            r"(<wp:positionV[^>]*>[\s\S]*?)<wp:posOffset>-?\d+</wp:posOffset>([\s\S]*?</wp:positionV>)",
            lambda m: (
                m.group(1)
                + f"<wp:posOffset>{round(patch['posOffsetY'])}</wp:posOffset>"
                + m.group(2)
            ),
            out,
            count=1,
        )
    return out


_WRAP_RE = re.compile(
    r"<wp:wrapNone\s*/>|<wp:wrapSquare[^>]*\/>|<wp:wrapSquare[\s\S]*?</wp:wrapSquare>|"
    r"<wp:wrapTight[\s\S]*?</wp:wrapTight>|<wp:wrapThrough[\s\S]*?</wp:wrapThrough>|"
    r"<wp:wrapTopAndBottom\s*/>|<wp:wrapTopAndBottom[\s\S]*?</wp:wrapTopAndBottom>"
)


def apply_image_wrap(xml: str, wrap, pos_offset=None, margin_align=None) -> str:
    has_anchor = bool(re.search(r"<wp:anchor[\s>]", xml))
    existing = _WRAP_RE.search(xml)
    existing_wrap = existing.group(0) if existing else None
    out = re.sub(r"<wp:simplePos[^>]*/>", "", xml)
    out = re.sub(r"<wp:positionH[\s\S]*?</wp:positionH>", "", out)
    out = re.sub(r"<wp:positionV[\s\S]*?</wp:positionV>", "", out)
    out = _WRAP_RE.sub("", out)
    if not wrap:
        if not has_anchor:
            return xml
        out = re.sub(
            r"<wp:anchor[^>]*>", '<wp:inline distT="0" distB="0" distL="0" distR="0">', out, count=1
        )
        return out.replace("</wp:anchor>", "</wp:inline>", 1)
    behind = "1" if wrap == "behind" else "0"
    is_side = wrap in (
        "square-left",
        "square-right",
        "tight-left",
        "tight-right",
        "through-left",
        "through-right",
    )
    if pos_offset is not None:
        position = (
            '<wp:simplePos x="0" y="0"/>'
            f'<wp:positionH relativeFrom="column"><wp:posOffset>{round(pos_offset["x"])}</wp:posOffset></wp:positionH>'
            f'<wp:positionV relativeFrom="paragraph"><wp:posOffset>{round(pos_offset["y"])}</wp:posOffset></wp:positionV>'
        )
    elif margin_align is not None:
        position = (
            '<wp:simplePos x="0" y="0"/>'
            f'<wp:positionH relativeFrom="margin"><wp:align>{escape_xml_text(margin_align["h"])}</wp:align></wp:positionH>'
            f'<wp:positionV relativeFrom="margin"><wp:align>{escape_xml_text(margin_align["v"])}</wp:align></wp:positionV>'
        )
    else:
        h_align = (
            "right" if wrap.endswith("-right") else ("center" if wrap == "topBottom" else "left")
        )
        position = (
            '<wp:simplePos x="0" y="0"/>'
            f'<wp:positionH relativeFrom="column"><wp:align>{h_align}</wp:align></wp:positionH>'
            '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        )
    keep_kind = (
        "wp:wrapTight"
        if wrap.startswith("tight-")
        else ("wp:wrapThrough" if wrap.startswith("through-") else None)
    )
    if keep_kind and existing_wrap and existing_wrap.startswith("<" + keep_kind):
        wrap_el = existing_wrap
    elif is_side:
        wrap_el = '<wp:wrapSquare wrapText="bothSides"/>'
    elif wrap == "topBottom":
        wrap_el = "<wp:wrapTopAndBottom/>"
    else:
        wrap_el = "<wp:wrapNone/>"
    anchor_open = (
        '<wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0"'
        f' relativeHeight="251658240" behindDoc="{behind}" locked="0" layoutInCell="1" allowOverlap="1">'
    )
    if has_anchor:
        out = re.sub(r"<wp:anchor[^>]*>", anchor_open, out, count=1)
    else:
        out = re.sub(r"<wp:inline[^>]*>", anchor_open, out, count=1).replace(
            "</wp:inline>", "</wp:anchor>", 1
        )
    out = re.sub(r"(<wp:anchor[^>]*>)", r"\1" + position, out, count=1)
    if "<wp:docPr" in out:
        return out.replace("<wp:docPr", wrap_el + "<wp:docPr", 1)
    return re.sub(r"(<a:graphic[\s>])", lambda m: wrap_el + m.group(0), out, count=1)


# ---------- TOC / подписи / индекс ----------
def generate_toc_field_xml(entries: list[dict]) -> list[str]:
    if not entries:
        return []
    normalized = []
    for entry in entries:
        try:
            level = min(max(int(entry["level"]), 1), 9)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("TOC entry level must be an integer from 1 to 9") from exc
        normalized.append({**entry, "level": level, "text": str(entry.get("text", ""))})
    entries = normalized
    max_level = max(e["level"] for e in entries)

    def p_pr(level):
        return (
            f'<w:pPr><w:pStyle w:val="TOC{level}"/>'
            '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9350"/></w:tabs>'
            "<w:rPr><w:noProof/></w:rPr></w:pPr>"
        )

    def entry_runs(text, page_no=None):
        x = (
            f'<w:r><w:rPr><w:noProof/></w:rPr><w:t xml:space="preserve">{escape_xml_text(text)}</w:t></w:r>'
            "<w:r><w:rPr><w:noProof/></w:rPr><w:tab/></w:r>"
        )
        if page_no is not None:
            x += f"<w:r><w:rPr><w:noProof/></w:rPr><w:t>{escape_xml_text(page_no)}</w:t></w:r>"
        return x

    begin = (
        '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> TOC \\o "1-{max_level}" \\h \\z \\u </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    )
    end = '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    out = []
    for i, e in enumerate(entries):
        first = begin if i == 0 else ""
        last = end if i == len(entries) - 1 else ""
        out.append(
            f"<w:p>{p_pr(e['level'])}{first}{entry_runs(e['text'], e.get('pageNo'))}{last}</w:p>"
        )
    return out


def generate_caption_xml(label: str, number: int, text: str) -> str:
    try:
        number = int(number)
    except (TypeError, ValueError) as exc:
        raise ValueError("caption number must be an integer") from exc
    label = str(label)
    text = str(text) if text is not None else ""
    r_pr = '<w:rPr><w:color w:val="44546A"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>'

    def run(inner):
        return f"<w:r>{r_pr}{inner}</w:r>"

    return (
        '<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:before="80" w:after="200"/></w:pPr>'
        + run(f'<w:t xml:space="preserve">{escape_xml_text(label)} </w:t>')
        + run('<w:fldChar w:fldCharType="begin" w:dirty="true"/>')
        + run(
            f'<w:instrText xml:space="preserve"> SEQ {escape_xml_text(label)} \\* ARABIC </w:instrText>'
        )
        + run('<w:fldChar w:fldCharType="separate"/>')
        + run(f"<w:t>{number}</w:t>")
        + run('<w:fldChar w:fldCharType="end"/>')
        + (run(f'<w:t xml:space="preserve"> {escape_xml_text(text)}</w:t>') if text else "")
        + "</w:p>"
    )


def generate_index_field_xml(terms: list[str]) -> list[str]:
    unique = sorted({t.strip() for t in terms if t.strip()})
    if not unique:
        return []
    begin = (
        '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> INDEX \\c "2" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    )
    end = '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    p_pr = (
        '<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="4300"/></w:tabs>'
        "<w:rPr><w:noProof/></w:rPr></w:pPr>"
    )
    out = []
    for i, term in enumerate(unique):
        first = begin if i == 0 else ""
        last = end if i == len(unique) - 1 else ""
        out.append(
            f"<w:p>{p_pr}{first}"
            f'<w:r><w:rPr><w:noProof/></w:rPr><w:t xml:space="preserve">{escape_xml_text(term)}</w:t></w:r>'
            f"<w:r><w:rPr><w:noProof/></w:rPr><w:tab/></w:r>{last}</w:p>"
        )
    return out


# ---------- DrawingML-генераторы (textbox / shape / wordart) ----------
def build_textbox_paragraph_xml(
    width_emu=1800000, height_emu=1080000, id_=1, fill_hex="FFFFFF", border_hex="000000"
) -> str:
    try:
        width_emu, height_emu, id_ = int(width_emu), int(height_emu), int(id_)
    except (TypeError, ValueError) as exc:
        raise ValueError("textbox width/height/id must be integers") from exc
    if width_emu <= 0 or height_emu <= 0 or id_ < 0:
        raise ValueError("textbox width/height must be positive and id non-negative")
    fill_hex, border_hex = str(fill_hex).upper(), str(border_hex).upper()
    if not re.fullmatch(r"[0-9A-F]{6}", fill_hex) or not re.fullmatch(r"[0-9A-F]{6}", border_hex):
        raise ValueError("textbox fill/border colors must be 6-digit RGB hex values")
    sp_pr = (
        f'<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{fill_hex}"/></a:solidFill>'
        f'<a:ln><a:solidFill><a:srgbClr val="{border_hex}"/></a:solidFill></a:ln></wps:spPr>'
    )
    wsp = (
        '<wps:wsp xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:cNvSpPr txBox="1"/>'
        + sp_pr
        + '<wps:txbx><w:txbxContent><w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p></w:txbxContent></wps:txbx>'
        "<wps:bodyPr/></wps:wsp>"
    )
    graphic = (
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">{wsp}</a:graphicData></a:graphic>'
    )
    anchor = (
        '<wp:anchor xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'distT="0" distB="0" distL="114300" distR="114300" simplePos="0" relativeHeight="251658240" '
        'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="column"><wp:align>center</wp:align></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapSquare wrapText="bothSides"/>'
        f'<wp:docPr id="{id_}" name="TextBox {id_}"/>' + graphic + "</wp:anchor>"
    )
    choice = f'<mc:Choice Requires="wps"><w:drawing>{anchor}</w:drawing></mc:Choice>'
    vml = (
        f'<v:rect xmlns:v="urn:schemas-microsoft-com:vml" style="position:absolute;'
        f'width:{width_emu / EMU_PER_PT}pt;height:{height_emu / EMU_PER_PT}pt" filled="t" stroked="t">'
        '<v:textbox><w:txbxContent><w:p><w:r><w:t xml:space="preserve"> </w:t></w:r></w:p></w:txbxContent></v:textbox></v:rect>'
    )
    fallback = f"<mc:Fallback><w:pict>{vml}</w:pict></mc:Fallback>"
    mc_ns = (
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
    )
    return f"<w:p><w:r><mc:AlternateContent {mc_ns}>{choice}{fallback}</mc:AlternateContent></w:r></w:p>"


WORDART_PRESETS = [
    {
        "id": "wordArt-1",
        "label": "Fill - accent color",
        "colorHex": "4472C4",
        "borderHex": "2F5496",
        "noFill": False,
    },
    {
        "id": "wordArt-2",
        "label": "Gradient - blue violet",
        "colorHex": "7B2FBE",
        "borderHex": "4472C4",
        "noFill": False,
    },
    {
        "id": "wordArt-3",
        "label": "Outline - no fill",
        "colorHex": "4472C4",
        "borderHex": "4472C4",
        "noFill": True,
    },
    {
        "id": "wordArt-4",
        "label": "Shadow - dark",
        "colorHex": "1F3864",
        "borderHex": "1F3864",
        "noFill": False,
    },
    {
        "id": "wordArt-5",
        "label": "Glow - orange",
        "colorHex": "ED7D31",
        "borderHex": "C55A11",
        "noFill": False,
    },
    {
        "id": "wordArt-6",
        "label": "Fill - dark red",
        "colorHex": "C00000",
        "borderHex": "9B0000",
        "noFill": False,
    },
]


def build_word_art_paragraph_xml(
    text="WordArt", word_art_id=None, width_emu=2700000, height_emu=720000, id_=1
) -> str:
    try:
        width_emu, height_emu, id_ = int(width_emu), int(height_emu), int(id_)
    except (TypeError, ValueError) as exc:
        raise ValueError("WordArt width/height/id must be integers") from exc
    if width_emu <= 0 or height_emu <= 0 or id_ < 0:
        raise ValueError("WordArt width/height must be positive and id non-negative")
    text = str(text)
    preset = next((p for p in WORDART_PRESETS if p["id"] == word_art_id), WORDART_PRESETS[0])
    r_pr = (
        f'<w:rPr><w:b/><w:color w:val="{preset["colorHex"]}"/>'
        '<w:sz w:val="72"/><w:szCs w:val="72"/></w:rPr>'
    )
    text_run = f'<w:r>{r_pr}<w:t xml:space="preserve">{escape_xml_text(text)}</w:t></w:r>'
    txbx = (
        f'<w:txbxContent><w:p><w:pPr><w:jc w:val="center"/></w:pPr>{text_run}</w:p></w:txbxContent>'
    )
    wsp = (
        '<wps:wsp xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        '<wps:cNvSpPr txBox="1"/>'
        f'<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width_emu}" cy="{height_emu}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></wps:spPr>'
        f"<wps:txbx>{txbx}</wps:txbx><wps:bodyPr/></wps:wsp>"
    )
    graphic = (
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        f'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">{wsp}</a:graphicData></a:graphic>'
    )
    anchor = (
        '<wp:anchor xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'distT="0" distB="0" distL="114300" distR="114300" simplePos="0" relativeHeight="251658240" '
        'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="column"><wp:align>center</wp:align></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{width_emu}" cy="{height_emu}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:wrapSquare wrapText="bothSides"/><wp:docPr id="{id_}" name="WordArt {id_}"/>'
        + graphic
        + "</wp:anchor>"
    )
    choice = f'<mc:Choice Requires="wps"><w:drawing>{anchor}</w:drawing></mc:Choice>'
    vml = (
        f'<v:rect xmlns:v="urn:schemas-microsoft-com:vml" style="position:absolute;'
        f'width:{width_emu / EMU_PER_PT}pt;height:{height_emu / EMU_PER_PT}pt" filled="f" stroked="f">'
        f"<v:textbox>{txbx}</v:textbox></v:rect>"
    )
    fallback = f"<mc:Fallback><w:pict>{vml}</w:pict></mc:Fallback>"
    mc_ns = (
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
    )
    return f"<w:p><w:r><mc:AlternateContent {mc_ns}>{choice}{fallback}</mc:AlternateContent></w:r></w:p>"
