"""Парсинг .docx в блочную модель."""

from __future__ import annotations

import base64
import contextlib
import io
import posixpath
import re
import zipfile

from . import chart as chart_mod
from .ink import find_ink_runs, strip_ink_runs
from .mathml import math_tokens_of, omml_fragments_of, omml_to_mathml
from .models import Block, CommentInfo, ParaFormat, ParsedDoc, RevisionInfo, Run
from .notes import NOTE_PART_PATH, parse_notes_xml
from .scan import scan_body
from .sources import parse_sources_xml
from .symbol_fonts import decode_symbol_char, decode_symbol_text
from .theme import THEME_PART_PATH, read_theme_colors, read_theme_fonts, resolve_theme_color
from .watermark import read_watermark_text
from .xml_utils import (
    attrs_of,
    bool_prop,
    children_of,
    children_through_sdt,
    find_child,
    find_children,
    name_of,
    serialize_xnode,
    text_of,
    underline_prop,
)
from .xml_utils import parse as xml_parse

IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "emf": "image/emf",
    "wmf": "image/wmf",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
EMU_PER_PX = 9525


def _resolve_part_target(base_dir: str, target: str) -> str:
    """Resolve an OPC relationship target to a normalized ZIP part path."""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, target))


def _dec(text: str) -> str:
    def num(m):
        cp = int(m.group(1) or m.group(2), 16 if m.group(1) else 10)
        if 0 <= cp <= 0x10FFFF and not (0xD800 <= cp <= 0xDFFF):
            try:
                return chr(cp)
            except ValueError:
                return m.group(0)
        return m.group(0)

    text = re.sub(r"&#(?:x([0-9a-f]+)|([0-9]+));", num, text, flags=re.I)
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _plain_text(xml: str) -> str:
    return _dec("".join(re.findall(r"<w:t(?:\s[^>]*)?>([\s\S]*?)</w:t>", xml)))


def _parse_rels(zf: zipfile.ZipFile, path: str) -> dict:
    rels = {}
    try:
        xml = zf.read(path).decode("utf-8")
    except KeyError:
        return rels
    try:
        parsed = xml_parse(xml)
    except Exception:
        return rels
    root = next((n for n in parsed if name_of(n) == "Relationships"), None)
    if not root:
        return rels
    for rel in find_children(root, "Relationship"):
        a = attrs_of(rel)
        if a.get("Id"):
            rels[a["Id"]] = {
                "target": a.get("Target", ""),
                "type": a.get("Type", ""),
                "targetMode": a.get("TargetMode"),
            }
    return rels


def _color_from(container, theme):
    if not container:
        return None
    a = attrs_of(find_child(container, "w:color") or {})
    if a.get("w:themeColor") and theme:
        r = resolve_theme_color(
            a["w:themeColor"], theme, a.get("w:themeTint"), a.get("w:themeShade")
        )
        if r:
            return r
    val = a.get("w:val")
    return val if val and val != "auto" else None


JC_ALIGN = {
    "left": "left",
    "start": "left",
    "center": "center",
    "right": "right",
    "end": "right",
    "both": "justify",
    "distribute": "distribute",
}


def _extract_para_format(p_pr) -> ParaFormat | None:
    fmt = ParaFormat()
    if bool_prop(p_pr, "w:bidi"):
        fmt.bidi = True
    jc = attrs_of(find_child(p_pr, "w:jc") or {}).get("w:val")
    if jc and jc in JC_ALIGN:
        fmt.align = JC_ALIGN[jc]
    if fmt.bidi and fmt.align in ("left", "right"):
        fmt.align = "right" if fmt.align == "left" else "left"
    spacing = find_child(p_pr, "w:spacing")
    if spacing:
        a = attrs_of(spacing)
        rule = a.get("w:lineRule", "auto")
        try:
            line = int(a.get("w:line", ""))
        except ValueError:
            line = 0
        if line > 0:
            fmt.line_raw_twips = line
            if rule == "auto":
                fmt.line_spacing = round(line / 240, 2)
                fmt.line_rule = "auto"
            else:
                fmt.line_rule = rule
        if a.get("w:before") is not None:
            try:
                before = int(a["w:before"])
                if before >= 0:
                    fmt.space_before = before
            except ValueError:
                pass
        if a.get("w:after") is not None:
            try:
                after = int(a["w:after"])
                if after >= 0:
                    fmt.space_after = after
            except ValueError:
                pass
    ind = find_child(p_pr, "w:ind")
    if ind:
        a = attrs_of(ind)
        left_raw = a.get("w:left", a.get("w:start"))
        if left_raw is not None:
            with contextlib.suppress(ValueError):
                fmt.indent_left = int(left_raw)
        right_raw = a.get("w:right", a.get("w:end"))
        if right_raw is not None:
            with contextlib.suppress(ValueError):
                fmt.indent_right = int(right_raw)
        if a.get("w:firstLine") is not None:
            with contextlib.suppress(ValueError):
                fmt.indent_first_line = int(a["w:firstLine"])
        elif a.get("w:hanging") is not None:
            with contextlib.suppress(ValueError):
                fmt.indent_first_line = -int(a["w:hanging"])
    if bool_prop(p_pr, "w:pageBreakBefore"):
        fmt.page_break_before = True
    if bool_prop(p_pr, "w:keepNext"):
        fmt.keep_next = True
    if bool_prop(p_pr, "w:keepLines"):
        fmt.keep_lines = True
    wc = find_child(p_pr, "w:widowControl")
    if wc and attrs_of(wc).get("w:val") in ("0", "false"):
        fmt.widow_control = False
    if bool_prop(p_pr, "w:contextualSpacing"):
        fmt.contextual_spacing = True
    shd = find_child(p_pr, "w:shd")
    if shd:
        fill = attrs_of(shd).get("w:fill")
        if fill and fill != "auto":
            fmt.shading_fill = fill
    p_bdr = find_child(p_pr, "w:pBdr")
    if p_bdr:
        borders = ""
        for side, ch in (("top", "t"), ("bottom", "b"), ("left", "l"), ("right", "r")):
            el = find_child(p_bdr, f"w:{side}")
            if el and attrs_of(el).get("w:val") != "none":
                borders += ch
        if borders:
            fmt.borders = borders
    tabs_el = find_child(p_pr, "w:tabs")
    if tabs_el:
        stops = []
        for tab in find_children(tabs_el, "w:tab"):
            a = attrs_of(tab)
            try:
                pos = int(a.get("w:pos", ""))
            except ValueError:
                continue
            val = a.get("w:val", "left")
            if val not in ("left", "center", "right", "decimal", "bar", "clear"):
                val = "left"
            stop = {"pos": pos, "val": val}
            leader = a.get("w:leader")
            if (
                leader
                and leader != "none"
                and leader in ("dot", "hyphen", "underscore", "heavy", "middleDot")
            ):
                stop["leader"] = leader
            stops.append(stop)
        if stops:
            fmt.tab_stops = stops
    frame = find_child(p_pr, "w:framePr")
    if frame:
        dc = attrs_of(frame).get("w:dropCap")
        if dc in ("drop", "margin"):
            try:
                lines = int(attrs_of(frame).get("w:lines", "3"))
            except ValueError:
                lines = 3
            fmt.drop_cap = {"type": dc, "lines": lines or 3}
    # Equality-based checks such as ``value not in (None, False, ...)`` make
    # numeric zero indistinguishable from False.  Zero is meaningful for
    # spacing/indents, so determine presence from each dataclass field's actual
    # default using identity semantics.
    has = False
    for name, field_info in fmt.__dataclass_fields__.items():
        value = getattr(fmt, name)
        if field_info.default is None:
            has = has or value is not None
        elif field_info.default is False:
            has = has or value is not False
        else:
            has = has or value != field_info.default
        if has:
            break
    return fmt if has else None


def _raw_p_pr_of(xml: str):
    open_end = xml.find(">") + 1
    if open_end == 0 or not xml.startswith("<w:pPr", open_end):
        return None
    start = open_end
    depth = 0
    for m in re.finditer(r"<w:pPr(?=[\s/>])|</w:pPr>", xml[start:], re.M):
        pos = start + m.start()
        if m.group(0) == "</w:pPr>":
            depth -= 1
            if depth == 0:
                return xml[start : pos + len("</w:pPr>")]
        else:
            gt = xml.find(">", pos)
            if gt != -1 and xml[gt - 1] == "/":
                if depth == 0:
                    return xml[start : gt + 1]
                continue
            depth += 1
    return None


def _build_run(r_node, link=None, theme=None) -> Run | None:
    text_parts = []
    for child in children_of(r_node):
        n = name_of(child)
        if n in ("w:t", "w:delText"):
            text_parts.append(text_of(child))
        elif n == "w:tab":
            text_parts.append("\t")
        elif n == "w:br":
            text_parts.append("\f" if attrs_of(child).get("w:type") == "page" else "\n")
        elif n == "w:cr":
            text_parts.append("\n")
        elif n == "w:noBreakHyphen":
            text_parts.append("\u2011")
        elif n == "w:sym":
            a = attrs_of(child)
            try:
                code = int(a.get("w:char", ""), 16)
            except ValueError:
                continue
            ch = decode_symbol_char(a.get("w:font", ""), code)
            text_parts.append(ch if ch else chr((code & 0xFF) + 0xF000))
    text = "".join(text_parts)
    if text == "":
        return None
    r_pr = find_child(r_node, "w:rPr")
    run = Run(text=text)
    if link:
        run.link = link
    if r_pr:
        run.raw_r_pr = serialize_xnode(r_pr)
        r_style = attrs_of(find_child(r_pr, "w:rStyle") or {}).get("w:val")
        if r_style and r_style != "Hyperlink":
            run.style_id = r_style
        run.bold = bool_prop(r_pr, "w:b")
        run.italic = bool_prop(r_pr, "w:i")
        run.underline = underline_prop(r_pr)
        run.strike = bool_prop(r_pr, "w:strike")
        color = _color_from(r_pr, theme)
        if color:
            run.color = color
        sz = attrs_of(find_child(r_pr, "w:sz") or {}).get("w:val")
        if sz:
            with contextlib.suppress(ValueError):
                run.size_half_points = int(sz)
        fonts = attrs_of(find_child(r_pr, "w:rFonts") or {})
        font = fonts.get("w:eastAsia") or fonts.get("w:ascii") or fonts.get("w:hAnsi")
        if font:
            run.font = font
        try:
            spc = int(attrs_of(find_child(r_pr, "w:spacing") or {}).get("w:val", ""))
            run.char_spacing_twips = spc
        except ValueError:
            pass
        try:
            scale = int(attrs_of(find_child(r_pr, "w:w") or {}).get("w:val", ""))
            if scale > 0:
                run.char_scale_pct = scale
        except ValueError:
            pass
        highlight = attrs_of(find_child(r_pr, "w:highlight") or {}).get("w:val")
        if highlight and highlight != "none":
            run.highlight = highlight
        va = attrs_of(find_child(r_pr, "w:vertAlign") or {}).get("w:val")
        if va in ("superscript", "subscript"):
            run.vert_align = va
        em = attrs_of(find_child(r_pr, "w:em") or {}).get("w:val")
        if em and em != "none":
            run.em = em
    if run.font:
        decoded = decode_symbol_text(run.font, run.text)
        if decoded is not None:
            run.text = decoded
            run.font = None
            if run.raw_r_pr:
                run.raw_r_pr = re.sub(r"<w:rFonts[^>]*/>", "", run.raw_r_pr)
    return run


def _same_style(a: Run, b: Run) -> bool:
    if a.note_ref or b.note_ref or a.xe_term is not None or b.xe_term is not None:
        return False
    if a.ref_field is not None or b.ref_field is not None:
        return False
    if a.instr_field is not None or b.instr_field is not None:
        return False
    if a.math or b.math:
        return False
    if a.ruby or b.ruby:
        return False
    return (
        (a.raw_r_pr or "") == (b.raw_r_pr or "")
        and a.style_id == b.style_id
        and bool(a.bold) == bool(b.bold)
        and bool(a.italic) == bool(b.italic)
        and bool(a.underline) == bool(b.underline)
        and bool(a.strike) == bool(b.strike)
        and a.color == b.color
        and a.size_half_points == b.size_half_points
        and a.font == b.font
        and a.char_spacing_twips == b.char_spacing_twips
        and a.char_scale_pct == b.char_scale_pct
        and a.highlight == b.highlight
        and a.vert_align == b.vert_align
        and a.em == b.em
        and (a.link or {}).get("href", "") == (b.link or {}).get("href", "")
        and (a.link or {}).get("tooltip") == (b.link or {}).get("tooltip")
        and (a.link or {}).get("rId") == (b.link or {}).get("rId")
        and (a.comment_ids or []) == (b.comment_ids or [])
    )


def _merge_runs(runs: list[Run]) -> list[Run]:
    merged = []
    for run in runs:
        prev = merged[-1] if merged else None
        if prev and _same_style(prev, run):
            prev.text += run.text
        else:
            merged.append(run)
    return merged


SIMPLE_INLINE_FIELD_RE = re.compile(
    r"^\s*(DATE|TIME|CREATEDATE|SAVEDATE|NUMPAGES|FILENAME|AUTHOR|PAGE)\b"
)


def _extract_runs(p_node, ctx, math_frags=None, ruby_frags=None) -> list[Run]:
    math_frags = math_frags or []
    ruby_frags = ruby_frags or []
    runs: list[Run] = []
    math_index = ruby_index = 0
    starts, ends = set(), set()

    def collect(nodes):
        for node in nodes:
            n = name_of(node)
            if n in ("w:commentRangeStart", "w:commentRangeEnd"):
                cid = attrs_of(node).get("w:id")
                if cid:
                    (starts if n == "w:commentRangeStart" else ends).add(cid)
            collect(children_of(node))

    collect(children_of(p_node))
    complete = starts & ends
    active = set()
    field_depth = 0
    field_instr = field_cached = ""
    field_separated = False

    def push(run: Run, rev=None):
        if active:
            run.comment_ids = sorted(active)
        if rev:
            if rev.get("ins"):
                run.ins = rev["ins"]
            if rev.get("del"):
                run.del_ = rev["del"]
        runs.append(run)

    def handle_run(node, link, rev):
        nonlocal field_depth, field_instr, field_cached, field_separated, math_index, ruby_index
        fld = find_child(node, "w:fldChar")
        if fld:
            t = attrs_of(fld).get("w:fldCharType")
            if t == "begin":
                field_depth += 1
                if field_depth == 1:
                    field_instr, field_separated, field_cached = "", False, ""
            elif t == "separate":
                if field_depth == 1:
                    field_separated = True
            elif t == "end":
                field_depth = max(0, field_depth - 1)
                if field_depth == 0:
                    xe = re.match(r'\s*XE\s+(?:"([^"]*)"|(\S+))', field_instr)
                    ref = re.match(r'\s*REF\s+(?:"([^"]+)"|([^\s\\]+))', field_instr)
                    if xe:
                        push(Run(text="", xe_term=xe.group(1) or xe.group(2)), rev)
                    elif ref:
                        name = ref.group(1) or ref.group(2)
                        push(Run(text=field_cached or name, ref_field=name), rev)
                    elif SIMPLE_INLINE_FIELD_RE.match(field_instr):
                        push(Run(text=field_cached or " ", instr_field=field_instr.strip()), rev)
                    field_instr, field_separated, field_cached = "", False, ""
            return
        if field_depth > 0:
            if find_child(node, "w:ruby"):
                ruby_index += 1
            instr = find_child(node, "w:instrText")
            if instr:
                field_instr += text_of(instr)
            elif field_separated and field_depth == 1:
                cached = _build_run(node, link, ctx.get("themeColors"))
                if cached:
                    field_cached += cached.text
            return
        ruby_node = find_child(node, "w:ruby")
        if ruby_node:
            xml_frag = ruby_frags[ruby_index] if ruby_index < len(ruby_frags) else None
            ruby_index += 1
            base = _ruby_part_text(ruby_node, "w:rubyBase")
            rt = _ruby_part_text(ruby_node, "w:rt")
            if base:
                push(
                    Run(text=base, ruby={"rt": rt, "xml": xml_frag})
                    if xml_frag
                    else Run(text=base),
                    rev,
                )
            return
        note_ref = find_child(node, "w:footnoteReference") or find_child(node, "w:endnoteReference")
        if note_ref:
            kind = "footnote" if name_of(note_ref) == "w:footnoteReference" else "endnote"
            nid = attrs_of(note_ref).get("w:id")
            if nid:
                num = ctx["noteNumbers"].get(f"{kind}:{nid}")
                push(Run(text=str(num or "*"), note_ref={"kind": kind, "id": nid}), rev)
                return
        run = _build_run(node, link, ctx.get("themeColors"))
        if run:
            push(run, rev)

    def walk(nodes, link=None, rev=None):
        nonlocal math_index
        for node in nodes:
            n = name_of(node)
            if n in ("w:commentRangeStart", "w:commentRangeEnd"):
                cid = attrs_of(node).get("w:id")
                if cid and cid in complete:
                    if n == "w:commentRangeStart":
                        active.add(cid)
                    else:
                        active.discard(cid)
            elif n in ("w:ins", "w:del", "w:moveFrom", "w:moveTo"):
                a = attrs_of(node)
                info = RevisionInfo(
                    author=a.get("w:author", ""), date=a.get("w:date"), id=a.get("w:id")
                )
                nr = dict(rev or {})
                if n == "w:ins" or n == "w:moveTo":
                    nr["ins"] = info
                else:
                    nr["del"] = info
                walk(children_of(node), link, nr)
            elif n == "w:r":
                handle_run(node, link, rev)
            elif n == "m:oMath":
                omml = math_frags[math_index] if math_index < len(math_frags) else None
                math_index += 1
                if omml:
                    push(Run(text="".join(math_tokens_of(omml)), math={"omml": omml}), rev)
            elif n == "w:hyperlink":
                a = attrs_of(node)
                r_id, anchor, tooltip = a.get("r:id"), a.get("w:anchor"), a.get("w:tooltip")
                href = (
                    ctx["rels"].get(r_id, {}).get("target", "")
                    if r_id
                    else (f"#{anchor}" if anchor else "")
                )
                lk = {"href": href}
                if r_id:
                    lk["rId"] = r_id
                if tooltip:
                    lk["tooltip"] = tooltip
                walk(children_of(node), lk, rev)
            elif n in ("w:smartTag", "w:sdt", "w:sdtContent"):
                walk(children_of(node), link, rev)

    walk(children_of(p_node))
    return _merge_runs(runs)


def _ruby_part_text(ruby_node, part):
    part_node = find_child(ruby_node, part)
    if not part_node:
        return ""
    out = []
    for r in children_of(part_node):
        if name_of(r) != "w:r":
            continue
        for c in children_of(r):
            if name_of(c) == "w:t":
                out.append(text_of(c))
    return "".join(out)


def _strip_textboxes(xml):
    return (
        re.sub(r"<w:txbxContent>[\s\S]*?</w:txbxContent>", "", xml)
        if "<w:txbxContent" in xml
        else xml
    )


def _image_meta(xml):
    meta = {}
    ext = re.search(r"<wp:extent[^>]*/?>", xml)
    if ext:
        cx = re.search(r'cx="(\d+)"', ext.group(0))
        cy = re.search(r'cy="(\d+)"', ext.group(0))
        if cx:
            meta["imageWidthPx"] = round(int(cx.group(1)) / EMU_PER_PX)
        if cy:
            meta["imageHeightPx"] = round(int(cy.group(1)) / EMU_PER_PX)
    jc = re.search(r'<w:jc w:val="([^"]+)"', xml)
    if jc:
        if jc.group(1) == "center":
            meta["imageAlign"] = "center"
        elif jc.group(1) in ("right", "end"):
            meta["imageAlign"] = "right"
    anchor = re.search(r"<wp:anchor[^>]*>", xml)
    if anchor:
        if 'behindDoc="1"' in anchor.group(0):
            meta["imageWrap"] = "behind"
        elif "<wp:wrapTopAndBottom" in xml:
            meta["imageWrap"] = "topBottom"
        else:
            wm = re.search(r"<wp:wrap(Square|Tight|Through)", xml)
            if wm:
                kind = wm.group(1)
                # Our writer positions side-wrapped images with positionH/align.
                # Prefer that stable signal; fall back to wrapText when reading
                # documents produced by other applications.
                pos_h = re.search(
                    r"<wp:positionH[^>]*>[\s\S]*?<wp:align>(left|right)</wp:align>[\s\S]*?</wp:positionH>",
                    xml,
                )
                side = pos_h.group(1) if pos_h else "left"
                if not pos_h:
                    wrap_tag = re.search(r"<wp:wrap(?:Square|Tight|Through)[^>]*>", xml)
                    if wrap_tag:
                        wt = re.search(r'wrapText="(left|right)"', wrap_tag.group(0))
                        # wrapText denotes the side where text appears, so the image
                        # sits on the opposite side.
                        if wt:
                            side = "right" if wt.group(1) == "left" else "left"
                meta["imageWrap"] = {
                    "Tight": f"tight-{side}",
                    "Through": f"through-{side}",
                    "Square": f"square-{side}",
                }[kind]
            else:
                meta["imageWrap"] = "front"
    return meta


def _media_data_url_sync(zf, rels, r_id):
    rel = rels.get(r_id)
    if not rel or rel.get("targetMode") == "External":
        return None
    path = _resolve_part_target("word", rel["target"])
    try:
        data = zf.read(path)
    except KeyError:
        return None
    mime = IMAGE_MIME.get(path.rsplit(".", 1)[-1].lower())
    if not mime:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _extract_image_sync(xml, ctx):
    m = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', xml) or re.search(
        r'<a:blip[^>]*r:link="([^"]+)"', xml
    )
    if not m:
        return None
    rel = ctx["rels"].get(m.group(1))
    if not rel:
        return None
    if rel.get("targetMode") == "External" or re.match(r"^https?://", rel["target"], re.I):
        return rel["target"]
    path = _resolve_part_target("word", rel["target"])
    try:
        data = ctx["zip"].read(path)
    except KeyError:
        return None
    mime = IMAGE_MIME.get(path.rsplit(".", 1)[-1].lower())
    if not mime or mime in ("image/emf", "image/wmf"):
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _only_xe_fields(xml):
    if "<w:fldSimple" in xml:
        return False
    instrs = re.findall(r"<w:instrText[^>]*>[\s\S]*?</w:instrText>", xml)
    if not instrs:
        return False
    for frag in instrs:
        text = _dec(re.sub(r"<[^>]+>", "", frag))
        if not (
            re.match(r'^\s*XE[\s"]', text)
            or re.match(r"^\s*REF\s", text)
            or SIMPLE_INLINE_FIELD_RE.match(text)
        ):
            return False
    return True


def _field_display_of(xml):
    m = re.search(r'<w:pStyle w:val="([^"]+)"', xml)
    style_id = m.group(1) if m else ""
    toc = re.match(r"^TOC([1-9])$", style_id, re.I)
    if toc:
        left = right = ""
        seen_tab = False
        for mm in re.finditer(r"<w:t(?:\s[^>]*)?>([\s\S]*?)</w:t>|<w:tab/>", xml):
            if mm.group(0) == "<w:tab/>":
                seen_tab = True
            elif seen_tab:
                right += mm.group(1)
            else:
                left += mm.group(1)
        anchor = re.search(r'<w:hyperlink [^>]*w:anchor="([^"]+)"', xml)
        d = {
            "kind": "tocLine",
            "left": _dec(left).strip(),
            "right": _dec(right).strip(),
            "level": int(toc.group(1)),
        }
        if anchor:
            d["anchor"] = anchor.group(1)
        return d
    visible = _plain_text(xml).strip()
    if visible == "" and re.search(r'<w:br\s[^>]*w:type="page"', xml):
        return {"kind": "pageBreak"}
    if visible:
        return {"kind": "text", "left": visible}
    return None


def _field_label(xml):
    instr_m = re.search(r"<w:instrText[^>]*>([\s\S]*?)</w:instrText>", xml)
    instr = instr_m.group(1) if instr_m else ""
    keyword = instr.strip().split()[0].upper() if instr.strip() else ""
    labels = {
        "TOC": "Auto TOC",
        "PAGE": "Page number field",
        "NUMPAGES": "Page count field",
        "REF": "Cross-reference field",
        "SEQ": "Caption number field",
        "HYPERLINK": "Hyperlink field",
        "DATE": "Date field",
    }
    if keyword in labels:
        return labels[keyword]
    if keyword:
        return f"Field ({keyword})"
    return "Field"


def _table_summary(xml):
    rows = len(re.findall(r"<w:tr[\s>]", xml))
    first = re.search(r"<w:tr[\s>][\s\S]*?</w:tr>", xml)
    cols = len(re.findall(r"<w:tc[\s>]", first.group(0))) if first else 0
    return {"label": f"Table {rows}×{cols}", "previewText": _plain_text(xml)[:120]}


def _extract_cell(tc, ctx):
    cell = {"paras": []}
    tc_pr = find_child(tc, "w:tcPr")
    if tc_pr:
        try:
            span = int(attrs_of(find_child(tc_pr, "w:gridSpan") or {}).get("w:val", "1"))
            if span > 1:
                cell["colSpan"] = span
        except ValueError:
            pass
        v_merge = find_child(tc_pr, "w:vMerge")
        if v_merge:
            cell["vMerge"] = (
                "restart" if attrs_of(v_merge).get("w:val") == "restart" else "continue"
            )
        h_merge = find_child(tc_pr, "w:hMerge")
        if h_merge:
            cell["hMerge"] = (
                "restart" if attrs_of(h_merge).get("w:val") == "restart" else "continue"
            )
        fill = attrs_of(find_child(tc_pr, "w:shd") or {}).get("w:fill")
        if fill and fill != "auto":
            cell["fill"] = fill
        va = attrs_of(find_child(tc_pr, "w:vAlign") or {}).get("w:val")
        if va in ("center", "bottom", "top"):
            cell["vAlign"] = va
    for p in children_through_sdt(tc, "w:p"):
        cell["paras"].append(text_of(p))
        if "align" not in cell:
            jc = attrs_of(find_child(find_child(p, "w:pPr") or {}, "w:jc") or {}).get("w:val")
            if jc in ("center", "right", "left"):
                cell["align"] = jc
            elif jc == "both":
                cell["align"] = "justify"
            elif jc == "start":
                cell["align"] = "left"
            elif jc == "end":
                cell["align"] = "right"
    while len(cell["paras"]) > 1 and cell["paras"][-1] == "":
        cell["paras"].pop()
    return cell


def _extract_table(xml, ctx):
    try:
        parsed = xml_parse(xml)
    except Exception:
        return None
    tbl = next((n for n in parsed if name_of(n) == "w:tbl"), None)
    if not tbl:
        return None
    grid = find_child(tbl, "w:tblGrid")
    col_pct = None
    if grid:
        widths = [float(attrs_of(c).get("w:w", 0) or 0) for c in find_children(grid, "w:gridCol")]
        total = sum(widths)
        if total > 0:
            col_pct = [w / total * 100 for w in widths]
    rows = []
    for tr in children_through_sdt(tbl, "w:tr"):
        cells = []
        for tc in children_through_sdt(tr, "w:tc"):
            cell = _extract_cell(tc, ctx)
            prev = cells[-1] if cells else None
            if cell.get("hMerge") == "continue" and prev:
                prev["colSpan"] = (prev.get("colSpan") or 1) + (cell.get("colSpan") or 1)
                continue
            cells.append(cell)
        if cells:
            rows.append(cells)
    if not rows:
        return None
    model = {"rows": rows}
    if col_pct:
        model["colWidthsPct"] = col_pct
    return model


def _sdt_meta(sdt_xml):
    pr_m = re.search(r"<w:sdtPr>([\s\S]*?)</w:sdtPr>", sdt_xml)
    pr = pr_m.group(1) if pr_m else ""
    alias_m = re.search(r"<w:alias[^>]*w:val=\"([^\"]*)\"", pr) or re.search(
        r'<w:alias[^>]*>\s*<[^>]*w:val="([^"]*)"', pr
    )
    tag_m = re.search(r"<w:tag[^>]*w:val=\"([^\"]*)\"", pr)
    alias = alias_m.group(1) if alias_m else ""
    tag = tag_m.group(1) if tag_m else ""
    control = "text"
    if re.search(r"<w:date[\s/>]", pr):
        control = "date"
    elif re.search(r"<w:dropDownList[\s/>]|<w:comboBox[\s/>]", pr):
        control = "dropdown"
    elif re.search(r"<w:checkbox[\s/>]", pr):
        control = "checkbox"
    return {"alias": alias, "tag": tag, "controlType": control}


def _build_block(el, index, xml, ctx) -> Block:
    base = {"id": f"b{index}", "docxIndex": index, "originalXml": xml}
    name = el["name"]
    if name == "w:sectPr":
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Section properties",
            hidden=True,
        )
    if name == "w:tbl":
        summ = _table_summary(xml)
        return Block(
            id=base["id"],
            type="table",
            docx_index=index,
            original_xml=xml,
            label=summ["label"],
            preview_text=summ["previewText"],
            table=_extract_table(xml, ctx),
        )
    if name == "w:sdt":
        content_open = re.search(r"<w:sdtContent(?:\s[^>]*)?>", xml)
        if content_open:
            close_idx = xml.find("</w:sdtContent>", content_open.end())
            if close_idx != -1:
                inner = xml[content_open.end() : close_idx]
                pm = re.search(r"<w:p[\s/>]", inner)
                if pm:
                    depth, p_end = 0, -1
                    for m2 in re.finditer(r"</?w:p(?=[\s/>])", inner[pm.start() :]):
                        if m2.group(0).startswith("</"):
                            if depth == 1:
                                p_end = pm.start() + m2.end() + 1
                                break
                            depth -= 1
                        else:
                            depth += 1
                    if p_end == -1:
                        p_end = len(inner)
                    p_xml = inner[pm.start() : p_end]
                    shell = _sdt_meta(xml)
                    shell.update(
                        {"openXml": xml[: content_open.end()], "closeXml": xml[close_idx:]}
                    )
                    inner_block = _build_block(
                        {"name": "w:p", "start": 0, "end": len(p_xml)}, index, p_xml, ctx
                    )
                    inner_block.original_xml = xml
                    inner_block.sdt_shell = shell
                    if not inner_block.label:
                        inner_block.label = shell["alias"] or shell["tag"] or "Content control"
                    return inner_block
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Content control",
            preview_text="",
        )
    if name != "w:p":
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label=name,
            preview_text="",
        )
    detect = strip_ink_runs(
        re.sub(r"<mc:Fallback>[\s\S]*?</mc:Fallback>", "", xml) if "<mc:Fallback" in xml else xml
    )
    if "<w:sectPr" in detect:
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Section break paragraph",
            preview_text=_plain_text(detect),
        )
    if "<w:drawing" in detect and "<c:chart" not in detect:
        img = _extract_image_sync(detect, ctx)
        if img:
            meta = _image_meta(detect)
            return Block(
                id=base["id"],
                type="image",
                docx_index=index,
                original_xml=xml,
                label="Image",
                image_data_url=img,
                image_width_px=meta.get("imageWidthPx"),
                image_height_px=meta.get("imageHeightPx"),
                image_align=meta.get("imageAlign"),
                image_wrap=meta.get("imageWrap"),
            )
    field_detect = _strip_textboxes(detect) if "<w:txbxContent" in detect else detect
    if (
        "<w:fldChar" in field_detect
        or "<w:fldSimple" in field_detect
        or "<w:instrText" in field_detect
    ) and not _only_xe_fields(detect):
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label=_field_label(xml),
            preview_text=_plain_text(xml),
            field_display=_field_display_of(xml),
        )
    if re.search(r'<w:pStyle w:val="TOC[1-9]"', xml):
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="TOC entry",
            preview_text=_plain_text(xml),
            field_display=_field_display_of(xml),
        )
    if re.search(r"<w:(delInstrText|cellIns|cellDel)[ />]", detect):
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Revised paragraph",
            preview_text=_plain_text(detect),
        )
    if "<m:oMath" in detect and ("<m:oMathPara" in detect or _plain_text(detect).strip() == ""):
        tokens = re.findall(r"<m:t(?:\s[^>]*)?>([\s\S]*?)</m:t>", detect)
        tokens = [_dec(t) for t in tokens]
        omml = "".join(omml_fragments_of(detect))
        mathml = omml_to_mathml(omml) if _plain_text(detect).strip() == "" else ""
        fd = {"tokens": tokens}
        if mathml:
            fd["mathml"] = mathml
        if omml:
            fd["omml"] = omml
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Equation",
            preview_text="".join(tokens),
            formula_display=fd,
        )
    if "<w:object" in detect or "<w:pict" in detect:
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Embedded object",
            preview_text=_plain_text(detect),
        )
    if "<w:drawing" in detect:
        if "<c:chart" in detect:
            cd = _extract_chart_sync(detect, ctx)
            return Block(
                id=base["id"],
                type="passthrough",
                docx_index=index,
                original_xml=xml,
                label="Chart",
                chart_display=cd,
                preview_text=(cd or {}).get("title", ""),
            )
        img = _extract_image_sync(detect, ctx)
        if img:
            meta = _image_meta(detect)
            return Block(
                id=base["id"],
                type="image",
                docx_index=index,
                original_xml=xml,
                label="Image",
                image_data_url=img,
                image_width_px=meta.get("imageWidthPx"),
                image_height_px=meta.get("imageHeightPx"),
                image_align=meta.get("imageAlign"),
                image_wrap=meta.get("imageWrap"),
            )
        if _plain_text(_strip_textboxes(detect)).strip() != "":
            return _build_text_paragraph(base, xml, ctx)
        cy = re.search(r'<wp:extent cx="\d+" cy="(\d+)"', xml)
        decorative = bool(cy and 0 < int(cy.group(1)) <= 130000)
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=index,
            original_xml=xml,
            label="Drawing object",
            decorative=decorative,
        )
    return _build_text_paragraph(base, xml, ctx)


def _extract_chart_sync(xml, ctx):
    m = re.search(r'<c:chart [^>]*r:id="([^"]+)"', xml)
    if not m:
        return None
    rel = ctx["rels"].get(m.group(1))
    if not rel or rel.get("targetMode") == "External":
        return None
    path = _resolve_part_target("word", rel["target"])
    try:
        part_xml = ctx["zip"].read(path).decode("utf-8")
    except KeyError:
        return None
    try:
        display = chart_mod.parse_chart_part_xml(part_xml, path)
    except Exception:
        # A malformed/unsupported chart must not make the entire DOCX
        # unparseable; it remains a passthrough drawing instead.
        return None
    if display:
        ctx["chartParts"][path] = part_xml
    return display


def _bookmark_names(xml):
    names, hidden = [], []
    for m in re.finditer(r'<w:bookmarkStart [^>]*w:name="([^"]+)"', xml):
        name = _dec(m.group(1))
        lst = hidden if name.startswith("_") else names
        if name not in lst:
            lst.append(name)
    return (names or None), (hidden or None)


def _cross_comment_markers(xml):
    def ids(pat):
        return [m.group(1) for m in re.finditer(pat, xml)]

    starts = ids(r'<w:commentRangeStart [^>]*w:id="([^"]+)"')
    ends = ids(r'<w:commentRangeEnd [^>]*w:id="([^"]+)"')
    only_starts = [i for i in starts if i not in ends]
    only_ends = [i for i in ends if i not in starts]
    return (only_starts or None), (only_ends or None)


def _build_text_paragraph(base, xml, ctx) -> Block:
    try:
        parsed = xml_parse(xml)
    except Exception:
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=base["docxIndex"],
            original_xml=xml,
            label="Unknown paragraph",
            preview_text=_plain_text(xml),
        )
    p_node = next((n for n in parsed if name_of(n) == "w:p"), None)
    if not p_node:
        return Block(
            id=base["id"],
            type="passthrough",
            docx_index=base["docxIndex"],
            original_xml=xml,
            label="Unknown paragraph",
            preview_text=_plain_text(xml),
        )
    p_pr = find_child(p_node, "w:pPr")
    style_id = attrs_of(find_child(p_pr, "w:pStyle") or {}).get("w:val") if p_pr else None
    fmt = _extract_para_format(p_pr) if p_pr else None
    raw_p_pr = _raw_p_pr_of(xml)
    math_xml = _strip_textboxes(
        re.sub(r"<mc:Fallback>[\s\S]*?</mc:Fallback>", "", xml) if "<mc:Fallback" in xml else xml
    )
    runs = _extract_runs(p_node, ctx, omml_fragments_of(math_xml))
    bookmarks, hidden_bookmarks = _bookmark_names(_strip_textboxes(xml))
    comment_starts, comment_ends = _cross_comment_markers(_strip_textboxes(xml))
    move_revision = None
    if re.search(r"<w:moveFrom[\s/>]", xml):
        move_revision = "from"
    elif re.search(r"<w:moveTo[\s/>]", xml):
        move_revision = "to"
    common = dict(
        id=base["id"],
        docx_index=base["docxIndex"],
        original_xml=xml,
        style_id=style_id,
        format=fmt,
        raw_p_pr=raw_p_pr,
        runs=runs,
        bookmarks=bookmarks,
        hidden_bookmarks=hidden_bookmarks,
        comment_starts=comment_starts,
        comment_ends=comment_ends,
        move_revision=move_revision,
    )
    num_pr = find_child(p_pr, "w:numPr") if p_pr else None
    if num_pr:
        num_id = attrs_of(find_child(num_pr, "w:numId") or {}).get("w:val")
        ilvl = attrs_of(find_child(num_pr, "w:ilvl") or {}).get("w:val", "0")
        if num_id:
            kind = ctx["numFormats"].get(num_id, "bullet")
            try:
                ilvl_i = int(ilvl)
            except ValueError:
                ilvl_i = 0
            return Block(
                type="listItem", list={"kind": kind, "numId": num_id, "ilvl": ilvl_i}, **common
            )
    if style_id:
        info = ctx["styles"].get(style_id)
        if info and info.get("headingLevel"):
            return Block(type="heading", level=info["headingLevel"], **common)
    return Block(type="paragraph", **common)


def _hf_content(xml, kind, theme=None):
    has_page = bool(re.search(r"<w:instrText[^>]*>[^<]*\bPAGE\b", xml))
    cleaned = re.sub(
        r'<w:fldChar w:fldCharType="separate"/>[\s\S]*?<w:fldChar w:fldCharType="end"/>', "", xml
    )
    cleaned = re.sub(
        r"<w:instrText[^>]*>[^<]*\bNUMPAGES\b[^<]*</w:instrText>", "<w:t>\ue000</w:t>", cleaned
    )
    cleaned = re.sub(r"<w:instrText[^>]*>[^<]*\bPAGE\b[^<]*</w:instrText>", "<w:t>#</w:t>", cleaned)
    return {
        "text": _plain_text(cleaned),
        "hasPageNumber": has_page,
        "watermark": read_watermark_text(xml) if kind == "header" else None,
    }


def _parse_styles(zf, theme):
    styles = {}
    try:
        xml = zf.read("word/styles.xml").decode("utf-8")
    except KeyError:
        return styles, None
    try:
        parsed = xml_parse(xml)
    except Exception:
        return styles, None
    root = next((n for n in parsed if name_of(n) == "w:styles"), None)
    if not root:
        return styles, None
    doc_defaults = None
    defaults = find_child(root, "w:docDefaults")
    if defaults:
        dd = {}
        r_pr = find_child(find_child(defaults, "w:rPrDefault") or {}, "w:rPr")
        if r_pr:
            sz = attrs_of(find_child(r_pr, "w:sz") or {}).get("w:val")
            if sz:
                with contextlib.suppress(ValueError):
                    dd["sizeHalfPoints"] = int(sz)
            rf = attrs_of(find_child(r_pr, "w:rFonts") or {})
            if rf.get("w:ascii"):
                dd["asciiFont"] = rf["w:ascii"]
            if rf.get("w:eastAsia"):
                dd["eastAsiaFont"] = rf["w:eastAsia"]
        p_pr = find_child(find_child(defaults, "w:pPrDefault") or {}, "w:pPr")
        if p_pr:
            sa = attrs_of(find_child(p_pr, "w:spacing") or {})
            try:
                line = int(sa.get("w:line", ""))
                rule = sa.get("w:lineRule", "auto")
                if line > 0:
                    dd["lineRawTwips"] = line
                    dd["lineRule"] = rule
                    if rule == "auto":
                        dd["lineSpacing"] = line / 240
            except ValueError:
                pass
            if sa.get("w:after") is not None:
                with contextlib.suppress(ValueError):
                    dd["spaceAfterTwips"] = int(sa["w:after"])
            if sa.get("w:before") is not None:
                with contextlib.suppress(ValueError):
                    dd["spaceBeforeTwips"] = int(sa["w:before"])
        if dd:
            doc_defaults = dd
    based_on = {}
    for style_node in find_children(root, "w:style"):
        a = attrs_of(style_node)
        stype = a.get("w:type")
        if stype not in ("paragraph", "character", "table"):
            continue
        sid = a.get("w:styleId")
        if not sid:
            continue
        name = attrs_of(find_child(style_node, "w:name") or {}).get("w:val") or sid
        heading_level = None
        if stype == "paragraph":
            nm = re.match(r"^heading\s*([1-9])$", name, re.I) or re.match(r"^Heading([1-9])$", sid)
            if nm:
                heading_level = int(nm.group(1))
            else:
                pp = find_child(style_node, "w:pPr")
                outline = (
                    attrs_of(find_child(pp, "w:outlineLvl") or {}).get("w:val") if pp else None
                )
                if outline is not None:
                    try:
                        lvl = int(outline)
                        if 0 <= lvl <= 8:
                            heading_level = lvl + 1
                    except ValueError:
                        pass
        bo = attrs_of(find_child(style_node, "w:basedOn") or {}).get("w:val")
        if bo:
            based_on[sid] = bo
        info = {"styleId": sid, "name": name, "type": stype, "headingLevel": heading_level}
        if bool_prop(style_node, "w:semiHidden"):
            info["semiHidden"] = True
        if bool_prop(style_node, "w:qFormat"):
            info["qFormat"] = True
        styles[sid] = info
    resolved = set()

    def resolve(sid, seen):
        info = styles.get(sid)
        if not info:
            return None
        parent_id = based_on.get(sid)
        if sid in resolved or not parent_id or sid in seen:
            return info
        seen.add(sid)
        parent = resolve(parent_id, seen)
        resolved.add(sid)
        if parent and parent.get("headingLevel") and not info.get("headingLevel"):
            info["headingLevel"] = parent["headingLevel"]
        return info

    for sid in list(styles.keys()):
        resolve(sid, set())
    return styles, doc_defaults


def _parse_numbering(zf):
    formats, defs = {}, {}
    try:
        xml = zf.read("word/numbering.xml").decode("utf-8")
    except KeyError:
        return formats, defs
    try:
        parsed = xml_parse(xml)
    except Exception:
        return formats, defs
    root = next((n for n in parsed if name_of(n) == "w:numbering"), None)
    if not root:
        return formats, defs
    abs_levels = {}
    for abs_ in find_children(root, "w:abstractNum"):
        abs_id = attrs_of(abs_).get("w:abstractNumId")
        if not abs_id:
            continue
        levels = {}
        for lvl in find_children(abs_, "w:lvl"):
            try:
                ilvl = int(attrs_of(lvl).get("w:ilvl", ""))
            except ValueError:
                continue
            start = 1
            with contextlib.suppress(ValueError):
                start = int(attrs_of(find_child(lvl, "w:start") or {}).get("w:val", "1"))
            levels[ilvl] = {
                "numFmt": attrs_of(find_child(lvl, "w:numFmt") or {}).get("w:val", "decimal"),
                "lvlText": attrs_of(find_child(lvl, "w:lvlText") or {}).get("w:val", ""),
                "start": start,
            }
        abs_levels[abs_id] = levels
    for num in find_children(root, "w:num"):
        num_id = attrs_of(num).get("w:numId")
        abs_id = attrs_of(find_child(num, "w:abstractNumId") or {}).get("w:val")
        if not num_id or not abs_id:
            continue
        levels = dict(abs_levels.get(abs_id, {}))
        overrides = {}
        for over in find_children(num, "w:lvlOverride"):
            try:
                ilvl = int(attrs_of(over).get("w:ilvl", ""))
            except ValueError:
                continue
            sv = attrs_of(find_child(over, "w:startOverride") or {}).get("w:val")
            if sv is not None:
                with contextlib.suppress(ValueError):
                    overrides[ilvl] = int(sv)
            lvl = find_child(over, "w:lvl")
            if lvl:
                try:
                    start = int(attrs_of(find_child(lvl, "w:start") or {}).get("w:val", "1"))
                except ValueError:
                    start = 1
                levels[ilvl] = {
                    "numFmt": attrs_of(find_child(lvl, "w:numFmt") or {}).get("w:val", "decimal"),
                    "lvlText": attrs_of(find_child(lvl, "w:lvlText") or {}).get("w:val", ""),
                    "start": start,
                }
        defs[num_id] = {
            "numId": num_id,
            "abstractNumId": abs_id,
            "levels": levels,
            "startOverrides": overrides,
        }
        formats[num_id] = "bullet" if levels.get(0, {}).get("numFmt") == "bullet" else "ordered"
    return formats, defs


def _parse_comments(zf) -> list[CommentInfo]:
    try:
        xml = zf.read("word/comments.xml").decode("utf-8")
    except KeyError:
        return []
    try:
        parsed = xml_parse(xml)
    except Exception:
        return []
    root = next((n for n in parsed if name_of(n) == "w:comments"), None)
    if not root:
        return []
    out = []
    for node in find_children(root, "w:comment"):
        a = attrs_of(node)
        if not a.get("w:id"):
            continue
        paras = find_children(node, "w:p")
        para_id = attrs_of(paras[-1]).get("w14:paraId") if paras else None
        c = CommentInfo(
            id=a["w:id"],
            author=a.get("w:author", ""),
            text="\n".join(text_of(p) for p in paras),
            initials=a.get("w:initials"),
            date=a.get("w:date"),
            para_id=para_id,
        )
        out.append(c)
    try:
        ext = zf.read("word/commentsExtended.xml").decode("utf-8")
    except KeyError:
        return out
    by_para = {c.para_id: c for c in out if c.para_id}
    for m in re.finditer(r"<w15:commentEx [^>]*/>", ext):
        tag = m.group(0)
        pid = re.search(r'w15:paraId="([^"]+)"', tag)
        parent = re.search(r'w15:paraIdParent="([^"]+)"', tag)
        done = bool(re.search(r'w15:done="(?:1|true)"', tag))
        c = by_para.get(pid.group(1)) if pid else None
        if not c:
            continue
        if done:
            c.done = True
        if parent:
            p = by_para.get(parent.group(1))
            if p:
                c.parent_id = p.id
    return out


def _parse_protection(zf):
    try:
        xml = zf.read("word/settings.xml").decode("utf-8")
    except KeyError:
        return None
    tag = re.search(r"<w:documentProtection[^>]*/>", xml)
    if not tag:
        return None
    t = tag.group(0)
    edit = re.search(r'w:edit="([^"]+)"', t)
    if not edit or edit.group(1) == "none":
        return None
    enf = re.search(r'w:enforcement="([^"]+)"', t)
    h = re.search(r'w:hash="([^"]+)"', t)
    salt = re.search(r'w:salt="([^"]+)"', t)
    spin = re.search(r'w:cryptSpinCount="(\d+)"', t)
    sid = re.search(r'w:cryptAlgorithmSid="(\d+)"', t)
    out = {"edit": edit.group(1), "enforced": enf.group(1) in ("1", "true") if enf else False}
    if h:
        out["hash"] = h.group(1)
    if salt:
        out["salt"] = salt.group(1)
    if spin:
        out["spinCount"] = int(spin.group(1))
    if sid:
        out["algorithmSid"] = int(sid.group(1))
    return out


def _read_hf_part(zf, document_xml, rels, kind, hf_type="default"):
    refs = re.findall(rf"<w:{kind}Reference[^>]*/>", document_xml)
    typed = next((r for r in refs if f'w:type="{hf_type}"' in r), None)
    ref = (
        typed
        if (hf_type != "default" or typed)
        else (typed or next((r for r in refs if 'w:type="' not in r), None))
        if hf_type == "default"
        else typed
    )
    if not ref:
        return None
    rid = re.search(r'r:id="([^"]+)"', ref)
    target = rels.get(rid.group(1), {}).get("target") if rid else None
    if not target:
        return None
    path = _resolve_part_target("word", target)
    try:
        xml = zf.read(path).decode("utf-8")
    except KeyError:
        return None
    return _hf_content(xml, kind)


def parse_docx(data: bytes) -> ParsedDoc:
    zf = zipfile.ZipFile(io.BytesIO(data))
    try:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    except KeyError as exc:
        raise ValueError("not a docx: missing word/document.xml") from exc
    theme_xml = None
    with contextlib.suppress(KeyError):
        theme_xml = zf.read(THEME_PART_PATH).decode("utf-8")
    theme_colors = read_theme_colors(theme_xml) if theme_xml else None
    theme_fonts = read_theme_fonts(theme_xml) if theme_xml else None
    styles, doc_defaults = _parse_styles(zf, theme_colors)
    heading_style_ids = {}
    list_paragraph_style_id = None
    for info in styles.values():
        if info.get("headingLevel") and info["headingLevel"] not in heading_style_ids:
            heading_style_ids[info["headingLevel"]] = info["styleId"]
        if not list_paragraph_style_id and re.match(r"^listparagraph$", info["styleId"], re.I):
            list_paragraph_style_id = info["styleId"]
    rels = _parse_rels(zf, "word/_rels/document.xml.rels")
    num_formats, numbering = _parse_numbering(zf)
    comments = _parse_comments(zf)
    protection = _parse_protection(zf)
    footnotes = (
        parse_notes_xml(zf.read(NOTE_PART_PATH["footnote"]).decode(), "footnote")
        if NOTE_PART_PATH["footnote"] in zf.namelist()
        else []
    )
    endnotes = (
        parse_notes_xml(zf.read(NOTE_PART_PATH["endnote"]).decode(), "endnote")
        if NOTE_PART_PATH["endnote"] in zf.namelist()
        else []
    )
    src_path = None
    for name in zf.namelist():
        if re.fullmatch(r"customXml/item\d+\.xml", name):
            xml = zf.read(name).decode("utf-8")
            if "Sources" in xml:
                src_path = name
                break
    sources = parse_sources_xml(zf.read(src_path).decode()) if src_path else []
    note_numbers = {}
    for i, n in enumerate(footnotes):
        note_numbers[f"footnote:{n.id}"] = i + 1
    for i, n in enumerate(endnotes):
        note_numbers[f"endnote:{n.id}"] = i + 1
    scan = scan_body(document_xml)
    ctx = {
        "zip": zf,
        "styles": styles,
        "rels": rels,
        "numFormats": num_formats,
        "chartParts": {},
        "noteNumbers": note_numbers,
        "themeColors": theme_colors,
    }
    blocks: list[Block] = []
    elements = []
    sdt_group_seq = 0
    for el in scan["elements"]:
        xml = document_xml[el["start"] : el["end"]]
        if el["name"] == "w:sdt":
            content_open = re.search(r"<w:sdtContent(?:\s[^>]*)?>", xml)
            if content_open:
                close_idx = xml.find("</w:sdtContent>", content_open.end())
                if close_idx != -1:
                    inner = xml[content_open.end() : close_idx]
                    children = []
                    depth, start, name = 0, -1, ""
                    for m in re.finditer(
                        r"<(/?)([A-Za-z0-9:._-]+)((?:\"[^\"]*\"|'[^']*'|[^\"'>])*)>", inner
                    ):
                        if m.group(1) == "/":
                            depth -= 1
                            if depth == 0:
                                children.append({"name": name, "start": start, "end": m.end()})
                        elif m.group(3).endswith("/"):
                            if depth == 0:
                                children.append(
                                    {"name": m.group(2), "start": m.start(), "end": m.end()}
                                )
                        else:
                            if depth == 0:
                                start, name = m.start(), m.group(2)
                            depth += 1
                    parts = [c for c in children if c["name"] in ("w:p", "w:tbl")]
                    if len(parts) >= 2:
                        meta = _sdt_meta(xml)
                        group = sdt_group_seq
                        sdt_group_seq += 1
                        for k, c in enumerate(parts):
                            i = len(elements)
                            p_start = el["start"] + content_open.end() + c["start"]
                            p_end = el["start"] + content_open.end() + c["end"]
                            elements.append({"name": c["name"], "start": p_start, "end": p_end})
                            child_xml = xml[
                                content_open.end() + c["start"] : content_open.end() + c["end"]
                            ]
                            block = _build_block(
                                {"name": c["name"], "start": 0, "end": len(child_xml)},
                                i,
                                child_xml,
                                ctx,
                            )
                            block.original_xml = child_xml
                            block.sdt_shell = {
                                **meta,
                                "group": group,
                                "openXml": xml[: content_open.end()] if k == 0 else "",
                                "closeXml": xml[close_idx:] if k == len(parts) - 1 else "",
                            }
                            if not block.label:
                                block.label = meta["alias"] or meta["tag"] or "Content control"
                            blocks.append(block)
                        continue
        i = len(elements)
        elements.append(el)
        blocks.append(_build_block(el, i, xml, ctx))
    header = _read_hf_part(zf, document_xml, rels, "header", "default")
    footer = _read_hf_part(zf, document_xml, rels, "footer", "default")
    title_pg = bool(re.search(r"<w:titlePg\s*/>", document_xml))
    try:
        settings_xml = zf.read("word/settings.xml").decode("utf-8")
        m = re.search(r"<w:evenAndOddHeaders[^>]*/>", settings_xml)
        even_odd = bool(m) and not re.search(r'w:val="(?:0|false)"', m.group(0))
    except KeyError:
        even_odd = False
    inks = []
    for block in blocks:
        if block.docx_index is None or not block.original_xml:
            continue
        for run in find_ink_runs(block.original_xml):
            data_url = None
            if run.get("embedRId"):
                data_url = _media_data_url_sync(zf, rels, run["embedRId"])
            inks.append(
                {
                    "anchorIndex": block.docx_index,
                    "offsetXPx": run["offsetXPx"],
                    "offsetYPx": run["offsetYPx"],
                    "widthPx": run["widthPx"],
                    "heightPx": run["heightPx"],
                    "dataUrl": data_url,
                    "payload": run["payload"],
                }
            )
    return ParsedDoc(
        blocks=blocks,
        comments=comments,
        footnotes=footnotes,
        endnotes=endnotes,
        sources=sources,
        inks=inks,
        protection=protection,
        styles=styles,
        doc_defaults=doc_defaults,
        heading_style_ids=heading_style_ids,
        list_paragraph_style_id=list_paragraph_style_id,
        numbering=numbering,
        theme_fonts=theme_fonts,
        theme_colors=theme_colors,
        watermark_text=header.get("watermark") if header else None,
        header_text=header.get("text") if header else None,
        footer_text=footer.get("text") if footer else None,
        footer_has_page_number=footer.get("hasPageNumber", False) if footer else False,
        title_pg=title_pg,
        even_and_odd_headers=even_odd,
        internal={
            "originalBytes": data,
            "documentXml": document_xml,
            "bodyInnerStart": scan["innerStart"],
            "bodyInnerEnd": scan["innerEnd"],
        },
        extras={"elements": elements, "chartParts": ctx["chartParts"]},
    )
