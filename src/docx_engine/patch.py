"""Patch-save: сборка итогового .docx."""

from __future__ import annotations

import base64
import datetime
import io
import math
import re
import zipfile

from .blank import BLANK_NUMBERING_XML, abstract_num_xml
from .chart import CHART_WORKBOOK_REL_TYPE, build_chart_part_xml, build_chart_workbook_xlsx
from .generate import apply_image_wrap, generate_paragraph_xml, inline_runs_xml
from .ink import (
    INK_MEDIA_PATH_RE,
    INK_MEDIA_PREFIX,
    INK_REL_RE,
    anchored_ink_run_xml,
    inject_ink_runs_into_paragraph,
    strip_ink_runs,
)
from .models import (
    TOTAL_PAGES_MARK,
    CommentInfo,
    GeneratedBlock,
    NoteInfo,
    ParaFormat,
    RevisionInfo,
    Run,
    SourceInfo,
)
from .notes import NOTE_CONTENT_TYPE, NOTE_PART_PATH, NOTE_REL_TYPE, build_notes_xml
from .section import apply_page_num_type, apply_section_settings, apply_section_start_type
from .sources import CUSTOM_XML_REL_TYPE, build_sources_item_props_xml, build_sources_xml
from .text_patch import patch_paragraph_texts
from .theme import (
    THEME_CONTENT_TYPE,
    THEME_PART_PATH,
    THEME_REL_TYPE,
    apply_theme_colors,
    apply_theme_fonts,
    build_theme_xml,
)
from .watermark import WATERMARK_NS, watermark_paragraph_xml
from .xml_utils import escape_xml_attr, escape_xml_text

HYPERLINK_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
HF_REL_TYPE = {
    "header": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
    "footer": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
}
NUMBERING_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
COMMENTS_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
COMMENTS_EXT_REL_TYPE = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
SETTINGS_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
CHART_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
CHART_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CORE_PROPS_PATH = "docProps/core.xml"
IMAGE_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/tiff": "tiff",
    "image/emf": "emf",
    "image/wmf": "wmf",
}
IMAGE_CONTENT_TYPE = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "emf": "image/emf",
    "wmf": "image/wmf",
}
EMU_PER_PX = 9525


def _max_rel_id(rels_xml):
    if not rels_xml:
        return 1000
    mx = 0
    for m in re.finditer(r'Id="rId(\d+)"', rels_xml):
        mx = max(mx, int(m.group(1)))
    return mx


def _next_image_seq(zf):
    mx = 0
    for name in zf.namelist():
        m = re.match(r"^word/media/necli(\d+)\.", name)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def _patch_core_props(xml, saved_at=None):
    if saved_at is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    else:
        raw = str(saved_at).strip()
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("savedAt must be a valid ISO-8601 timestamp") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
    iso = (
        dt.astimezone(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    out = re.sub(
        r"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
        lambda m: m.group(1) + escape_xml_text(iso) + m.group(2),
        xml,
    )

    def bump(m):
        try:
            return f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}"
        except ValueError:
            return m.group(0)

    out = re.sub(r"(<cp:revision>)(\d+)(</cp:revision>)", bump, out)
    return None if out == xml else out


def _build_comments_xml(comments, original_xml=None):
    originals = {}
    if original_xml:
        for m in re.finditer(r"<w:comment\s[^>]*>[\s\S]*?</w:comment>", original_xml):
            cid = re.search(r'w:id="([^"]+)"', m.group(0))
            if cid:
                paras = []
                for p in re.finditer(r"<w:p[\s>][\s\S]*?</w:p>|<w:p/>", m.group(0)):
                    texts = re.findall(r"<w:t(?:\s[^>]*)?>([\s\S]*?)</w:t>", p.group(0))
                    paras.append(
                        "".join(texts)
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                        .replace("&quot;", '"')
                        .replace("&apos;", "'")
                        .replace("&amp;", "&")
                    )
                originals[cid.group(1)] = {"text": "\n".join(paras), "xml": m.group(0)}
    body = []
    for c in comments:
        orig = originals.get(c.id if hasattr(c, "id") else c["id"])
        c_id = c.id if hasattr(c, "id") else c["id"]
        c_author = c.author if hasattr(c, "author") else c["author"]
        c_text = c.text if hasattr(c, "text") else c["text"]
        c_initials = c.initials if hasattr(c, "initials") else c.get("initials")
        c_date = c.date if hasattr(c, "date") else c.get("date")
        c_para = c.para_id if hasattr(c, "para_id") else c.get("paraId")
        if orig and orig["text"] == c_text:
            body.append(orig["xml"])
            continue
        if orig:
            patched = patch_paragraph_texts(orig["xml"], c_text)
            if patched is not None:
                body.append(patched)
                continue
        attrs = f'w:id="{escape_xml_attr(c_id)}" w:author="{escape_xml_attr(c_author)}"'
        if c_initials:
            attrs += f' w:initials="{escape_xml_attr(c_initials)}"'
        if c_date:
            attrs += f' w:date="{escape_xml_attr(c_date)}"'
        lines = c_text.split("\n")
        paras = []
        for i, line in enumerate(lines):
            pid = (
                f' w14:paraId="{escape_xml_attr(c_para)}"' if i == len(lines) - 1 and c_para else ""
            )
            paras.append(
                f'<w:p{pid}><w:r><w:t xml:space="preserve">{escape_xml_text(line)}</w:t></w:r></w:p>'
            )
        body.append(f"<w:comment {attrs}>{''.join(paras)}</w:comment>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">'
        f"{''.join(body)}</w:comments>"
    )


def _build_comments_extended(comments):
    para_of = {}
    for c in comments:
        cid = c.id if hasattr(c, "id") else c["id"]
        pid = c.para_id if hasattr(c, "para_id") else c.get("paraId")
        para_of[cid] = pid
    body = []
    for c in comments:
        pid = c.para_id if hasattr(c, "para_id") else c.get("paraId")
        if not pid:
            continue
        parent = c.parent_id if hasattr(c, "parent_id") else c.get("parentId")
        done = c.done if hasattr(c, "done") else c.get("done", False)
        parent_attr = (
            f' w15:paraIdParent="{escape_xml_attr(para_of[parent])}"'
            if parent and para_of.get(parent)
            else ""
        )
        body.append(
            f'<w15:commentEx w15:paraId="{escape_xml_attr(pid)}"{parent_attr} w15:done="{"1" if done else "0"}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<w15:commentsEx xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        ' xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" mc:Ignorable="w15">'
        f"{''.join(body)}</w15:commentsEx>"
    )


def _with_watermark_ns(open_tag):
    out = open_tag
    for ns in (
        'xmlns:v="urn:schemas-microsoft-com:vml"',
        'xmlns:o="urn:schemas-microsoft-com:office:office"',
        'xmlns:w10="urn:schemas-microsoft-com:office:word"',
    ):
        if ns.split("=")[0] + "=" not in out:
            out = out[:-1] + " " + ns + ">"
    return out


def _header_footer_part_xml(kind, hf, watermark=None, original_xml=None):
    root = "w:hdr" if kind == "header" else "w:ftr"

    def text_run(t):
        return f'<w:r><w:t xml:space="preserve">{escape_xml_text(t)}</w:t></w:r>' if t else ""

    page_field = (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    num_pages_field = (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> NUMPAGES </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    text = hf.get("text", "") if isinstance(hf, dict) else hf.text
    page_number = hf.get("pageNumber") if isinstance(hf, dict) else getattr(hf, "page_number", None)
    paras = hf.get("paras") if isinstance(hf, dict) else getattr(hf, "paras", None)
    if paras:
        page_emitted = not page_number
        parts = []
        for para in paras:
            align = para.get("align") if isinstance(para, dict) else getattr(para, "align", None)
            jc_val = "both" if align == "justify" else align
            jc = f'<w:pPr><w:jc w:val="{escape_xml_attr(jc_val)}"/></w:pPr>' if jc_val else ""
            runs_xml = ""
            runs = para.get("runs", []) if isinstance(para, dict) else getattr(para, "runs", [])
            for run in runs:
                run_model = (
                    run
                    if isinstance(run, Run)
                    else _coerce_generated_block({"type": "paragraph", "runs": [run]}).runs[0]
                )
                r_text = run_model.text

                def styled(text, _run_model=run_model):
                    import copy

                    clone = copy.copy(_run_model)
                    clone.text = text
                    # A field wrapper itself cannot carry nested field metadata/revisions.
                    clone.ref_field = None
                    clone.instr_field = None
                    clone.xe_term = None
                    clone.math = None
                    clone.ruby = None
                    return inline_runs_xml([clone]) if text else ""

                if TOTAL_PAGES_MARK in r_text or (not page_emitted and "#" in r_text):
                    for k, seg in enumerate(r_text.split(TOTAL_PAGES_MARK)):
                        if k > 0:
                            runs_xml += num_pages_field
                        if not page_emitted and "#" in seg:
                            before, *rest = seg.split("#")
                            runs_xml += styled(before)
                            runs_xml += page_field
                            runs_xml += styled("#".join(rest))
                            page_emitted = True
                        else:
                            runs_xml += styled(seg)
                else:
                    runs_xml += inline_runs_xml([run_model])
            parts.append(f"<w:p>{jc}{runs_xml}</w:p>")
        content = "".join(parts)
        if not page_emitted:
            content += f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>{page_field}</w:p>'
    else:

        def text_with_total(t):
            return "".join(
                text_run(seg) if i == 0 else num_pages_field + text_run(seg)
                for i, seg in enumerate(t.split(TOTAL_PAGES_MARK))
            )

        runs = []
        if page_number and "#" in text:
            before, *rest = text.split("#")
            runs.append(text_with_total(before))
            runs.append(page_field)
            runs.append(text_with_total("#".join(rest)))
        else:
            if text:
                runs.append(text_with_total(text + (" " if page_number else "")))
            if page_number:
                runs.append(page_field)
        content = f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>{"".join(runs)}</w:p>'
    watermark_xml = watermark_paragraph_xml(watermark) if kind == "header" and watermark else ""
    body = watermark_xml + content
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<{root} xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        f"{WATERMARK_NS}>{body}</{root}>"
    )


def _patch_header_watermark(original_xml: str, watermark) -> str:
    """Add/replace/remove our VML watermark without rebuilding the header."""
    if not original_xml:
        return original_xml
    # Remove only paragraphs containing the engine's own watermark shape ID;
    # preserve unrelated VML/images/tables/content controls in the header.
    out = re.sub(
        r"<w:p\b[^>]*>(?:(?!</w:p>)[\s\S])*?PowerPlusWaterMarkObject1(?:(?!</w:p>)[\s\S])*?</w:p>",
        "",
        original_xml,
    )
    if not watermark:
        return out
    m = re.search(r"<w:hdr\b[^>]*>", out)
    if not m:
        return out
    open_tag = _with_watermark_ns(m.group(0))
    out = out[: m.start()] + open_tag + out[m.end() :]
    insert_at = m.start() + len(open_tag)
    return out[:insert_at] + watermark_paragraph_xml(str(watermark)) + out[insert_at:]


def _apply_protection(xml, protection):
    out = re.sub(r"<w:documentProtection[^>]*/>", "", xml)
    if protection:
        edit = str(protection.get("edit", "readOnly"))
        if edit not in {"readOnly", "comments", "trackedChanges", "forms"}:
            raise ValueError("protection edit must be readOnly, comments, trackedChanges, or forms")
        crypt = ""
        if protection.get("hash"):
            try:
                spin = int(protection.get("spinCount", 100000))
                sid = int(protection.get("algorithmSid", 14))
            except (TypeError, ValueError) as exc:
                raise ValueError("protection spinCount/algorithmSid must be integers") from exc
            if not 0 <= spin <= 10_000_000:
                raise ValueError("protection spinCount must be between 0 and 10000000")
            if sid < 0:
                raise ValueError("protection algorithmSid must be non-negative")
            for field in ("hash", "salt"):
                value = protection.get(field)
                if value:
                    try:
                        base64.b64decode(str(value), validate=True)
                    except Exception as exc:
                        raise ValueError(f"protection {field} must be valid base64") from exc
            crypt = (
                ' w:cryptProviderType="rsaAES" w:cryptAlgorithmClass="hash" w:cryptAlgorithmType="typeAny"'
                f' w:cryptAlgorithmSid="{sid}"'
                f' w:cryptSpinCount="{spin}"'
                f' w:hash="{escape_xml_attr(protection["hash"])}"'
                + (
                    f' w:salt="{escape_xml_attr(protection["salt"])}"'
                    if protection.get("salt")
                    else ""
                )
            )
        tag = (
            f'<w:documentProtection w:edit="{escape_xml_attr(edit)}"'
            + (' w:enforcement="1"' if protection.get("enforced") else "")
            + crypt
            + "/>"
        )
        out = re.sub(r"(<w:settings[^>]*>)", lambda m: m.group(1) + tag, out, count=1)
    return out


def _apply_title_pg(sect_pr_xml, on):
    xml = re.sub(r"<w:titlePg[^>]*/>", "", sect_pr_xml)
    if on:
        if "<w:docGrid" in xml:
            xml = xml.replace("<w:docGrid", "<w:titlePg/><w:docGrid", 1)
        else:
            xml = xml.replace("</w:sectPr>", "<w:titlePg/></w:sectPr>", 1)
    return xml


def _apply_even_odd(xml, on):
    out = re.sub(r"<w:evenAndOddHeaders[^>]*/>", "", xml)
    return re.sub(r"(<w:settings[^>]*>)", r"\1<w:evenAndOddHeaders/>", out, count=1) if on else out


def _apply_page_color(document_xml, color):
    xml = re.sub(r"<w:background[^>]*/>", "", document_xml)
    if color:
        value = str(color).strip()
        if value.lower() == "auto":
            value = "auto"
        elif not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            raise ValueError("pageColor must be 'auto' or a 6-digit RGB hex color")
        else:
            value = value.upper()
        xml = re.sub(
            r"(<w:document[^>]*>)",
            lambda m: m.group(1) + f'<w:background w:color="{escape_xml_attr(value)}"/>',
            xml,
            count=1,
        )
    return xml


def _build_style_xml(up):
    r_pr = []
    rpr = up.get("rPr") or {}
    if rpr.get("font"):
        f = escape_xml_attr(rpr["font"])
        r_pr.append(f'<w:rFonts w:ascii="{f}" w:hAnsi="{f}" w:eastAsia="{f}"/>')
    if rpr.get("bold"):
        r_pr.append("<w:b/>")
    if rpr.get("italic"):
        r_pr.append("<w:i/>")
    if rpr.get("strike"):
        r_pr.append("<w:strike/>")
    if rpr.get("color"):
        r_pr.append(f'<w:color w:val="{escape_xml_attr(rpr["color"])}"/>')
    if rpr.get("sizeHalfPoints"):
        size = int(rpr["sizeHalfPoints"])
        if size <= 0:
            raise ValueError("style sizeHalfPoints must be positive")
        r_pr.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if rpr.get("underline"):
        r_pr.append('<w:u w:val="single"/>')
    p_pr = []
    sp = up.get("pPr") or {}
    if (
        sp.get("spaceBeforeTwips") is not None
        or sp.get("spaceAfterTwips") is not None
        or sp.get("lineSpacing") is not None
    ):
        attrs = []
        if sp.get("spaceBeforeTwips") is not None:
            attrs.append(f' w:before="{int(sp["spaceBeforeTwips"])}"')
        if sp.get("spaceAfterTwips") is not None:
            attrs.append(f' w:after="{int(sp["spaceAfterTwips"])}"')
        if sp.get("lineSpacing") is not None:
            attrs.append(f' w:line="{round(float(sp["lineSpacing"]) * 240)}" w:lineRule="auto"')
        p_pr.append(f"<w:spacing{''.join(attrs)}/>")
    if sp.get("align"):
        jc = "both" if sp["align"] == "justify" else sp["align"]
        p_pr.append(f'<w:jc w:val="{escape_xml_attr(jc)}"/>')
    style_type = str(up["type"])
    if style_type not in ("paragraph", "character", "table", "numbering"):
        raise ValueError("style type must be paragraph, character, table, or numbering")
    return (
        f'<w:style w:type="{escape_xml_attr(style_type)}" w:styleId="{escape_xml_attr(up["styleId"])}" w:customStyle="1">'
        f'<w:name w:val="{escape_xml_attr(up["name"])}"/>'
        + (f'<w:basedOn w:val="{escape_xml_attr(up["basedOn"])}"/>' if up.get("basedOn") else "")
        + "<w:qFormat/>"
        + (f"<w:pPr>{''.join(p_pr)}</w:pPr>" if p_pr else "")
        + (f"<w:rPr>{''.join(r_pr)}</w:rPr>" if r_pr else "")
        + "</w:style>"
    )


def save_docx(parsed, final_blocks: list[dict], options: dict | None = None) -> bytes:
    """final_blocks: [{'kind':'original','docxIndex':n} | {'kind':'generated','block':GeneratedBlock}
    | {'kind':'xml','xml':str} | {'kind':'image','image':{...}} | {'kind':'chart','chart':{...}}]"""
    options = options or {}
    internal = parsed.internal
    document_xml = internal["documentXml"]
    original_bytes = internal["originalBytes"]
    body_start, body_end = internal["bodyInnerStart"], internal["bodyInnerEnd"]
    elements = parsed.extras["elements"]
    visible_order = [b.docx_index for b in parsed.blocks if not b.hidden]

    def _opt_unset():
        keys = (
            "section",
            "sectionStartType",
            "pgNumType",
            "pageColor",
            "header",
            "footer",
            "headerFirst",
            "footerFirst",
            "headerEven",
            "footerEven",
            "titlePg",
            "numbering",
            "styleUpserts",
            "evenAndOddHeaders",
            "comments",
            "protection",
            "footnotes",
            "endnotes",
            "watermark",
            "inks",
            "sources",
            "themeFonts",
            "themeColors",
            "partXml",
            "partBinary",
            "savedAt",
        )
        return all(options.get(k) is None for k in keys)

    unchanged = (
        len(final_blocks) == len(visible_order)
        and all(
            fb.get("kind") == "original"
            and fb.get("docxIndex") == visible_order[i]
            and fb.get("revision") is None
            for i, fb in enumerate(final_blocks)
        )
        and options.get("sectionHf") is None
        and _opt_unset()
    )
    if unchanged:
        return original_bytes
    zf = zipfile.ZipFile(io.BytesIO(original_bytes))
    rels_path = "word/_rels/document.xml.rels"
    try:
        rels_xml = zf.read(rels_path).decode("utf-8")
    except KeyError:
        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
        )
    new_rels = []
    next_rel = _max_rel_id(rels_xml) + 1

    def alloc_hyperlink(href):
        existing = next((r for r in new_rels if r["external"] and r["target"] == href), None)
        if existing:
            return existing["rId"]
        nonlocal_holder[0] += 1
        r_id = f"rId{nonlocal_holder[0]}"
        new_rels.append({"rId": r_id, "type": HYPERLINK_REL_TYPE, "target": href, "external": True})
        return r_id

    nonlocal_holder = [next_rel - 1]
    gen_ctx = {
        "headingStyleIds": parsed.heading_style_ids,
        "listParagraphStyleId": parsed.list_paragraph_style_id,
        "allocateHyperlinkRel": alloc_hyperlink,
    }
    new_media = []
    used_ext = set()
    image_seq = _next_image_seq(zf)

    def embed_image(image):
        nonlocal image_seq
        mime = str(image.get("mime") or "").lower()
        ext = IMAGE_EXT.get(mime)
        if not ext:
            raise ValueError(f"unsupported image MIME type: {mime or '<empty>'}")
        try:
            width_px = float(image["widthPx"])
            height_px = float(image["heightPx"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("image widthPx/heightPx must be numeric") from exc
        if (
            not math.isfinite(width_px)
            or not math.isfinite(height_px)
            or width_px <= 0
            or height_px <= 0
        ):
            raise ValueError("image widthPx/heightPx must be finite positive numbers")
        try:
            image_b64 = str(image["base64"])
            base64.b64decode(image_b64, validate=True)
        except (KeyError, ValueError, TypeError, base64.binascii.Error) as exc:
            raise ValueError("image base64 must be valid base64") from exc
        align = image.get("align")
        if align not in (None, "left", "center", "right"):
            raise ValueError("image align must be left, center, or right")
        media_path = f"word/media/necli{image_seq}.{ext}"
        image_seq += 1
        nonlocal_holder[0] += 1
        r_id = f"rId{nonlocal_holder[0]}"
        new_rels.append(
            {
                "rId": r_id,
                "type": IMAGE_REL_TYPE,
                "target": media_path.replace("word/", "", 1),
                "external": False,
            }
        )
        new_media.append({"path": media_path, "base64": image_b64})
        used_ext.add(ext)
        cx = max(1, round(width_px * EMU_PER_PX))
        cy = max(1, round(height_px * EMU_PER_PX))
        doc_pr_id = 9000 + image_seq
        p_pr = f'<w:pPr><w:jc w:val="{align}"/></w:pPr>' if align and align != "left" else ""
        xml = (
            f'<w:p>{p_pr}<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="{doc_pr_id}" name="Picture {doc_pr_id}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{doc_pr_id}" name="Picture {doc_pr_id}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{r_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )
        return apply_image_wrap(xml, image["wrap"]) if image.get("wrap") else xml

    new_chart_parts = []
    new_chart_wbs = []
    chart_doc_pr = [8000]

    def embed_chart(chart):
        n = 1
        while f"word/charts/chart{n}.xml" in zf.namelist() or any(
            p["path"] == f"word/charts/chart{n}.xml" for p in new_chart_parts
        ):
            n += 1
        path = f"word/charts/chart{n}.xml"
        nonlocal_holder[0] += 1
        r_id = f"rId{nonlocal_holder[0]}"
        new_rels.append(
            {
                "rId": r_id,
                "type": CHART_REL_TYPE,
                "target": f"charts/chart{n}.xml",
                "external": False,
            }
        )
        wb_b64 = build_chart_workbook_xlsx(chart["categories"], chart["series"])
        wb_r_id = "rId1"
        chart_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="{wb_r_id}" Type="{CHART_WORKBOOK_REL_TYPE}" '
            f'Target="embeddings/workbook{n}.xlsx"/></Relationships>'
        )
        new_chart_parts.append({"path": path, "xml": build_chart_part_xml(chart, wb_r_id)})
        new_chart_wbs.append(
            {
                "xlsxPath": f"word/charts/embeddings/workbook{n}.xlsx",
                "relsPath": f"word/charts/_rels/chart{n}.xml.rels",
                "relsXml": chart_rels,
                "base64": wb_b64,
            }
        )
        doc_pr_id = chart_doc_pr[0]
        chart_doc_pr[0] += 1
        return (
            '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="5486400" cy="3200400"/>'
            f'<wp:docPr id="{doc_pr_id}" name="Chart {doc_pr_id}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            f'<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="{r_id}"/>'
            "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    inks_by_block = {}
    for ink in options.get("inks") or []:
        inks_by_block.setdefault(ink["blockIndex"], []).append(ink)
    ink_seq = [1]

    def ink_run_xml(ink):
        media_path = f"word/media/{INK_MEDIA_PREFIX}{ink_seq[0]}.png"
        ink_seq[0] += 1
        nonlocal_holder[0] += 1
        r_id = f"rId{nonlocal_holder[0]}"
        new_rels.append(
            {
                "rId": r_id,
                "type": IMAGE_REL_TYPE,
                "target": media_path.replace("word/", "", 1),
                "external": False,
            }
        )
        new_media.append({"path": media_path, "base64": ink["base64"]})
        used_ext.add("png")
        return anchored_ink_run_xml(ink, r_id, 9000 + ink_seq[0])

    sect_block = next(
        (b for b in parsed.blocks if b.hidden and b.original_xml and "<w:sectPr" in b.original_xml),
        None,
    )
    trailing_sect = sect_block.original_xml if sect_block else ""
    rel_targets = {}
    for tag in re.findall(r"<Relationship [^>]*/>", rels_xml):
        rid = re.search(r'Id="([^"]+)"', tag)
        target = re.search(r'Target="([^"]+)"', tag)
        if rid and target:
            rel_targets[rid.group(1)] = target.group(1)
    hf_parts = []
    hf_ref_tags = []
    hf_overrides = []

    def plan_header_footer(kind, hf, watermark=None, hf_type="default", watermark_only=False):
        if hf is None and watermark is None and not watermark_only:
            return
        refs = re.findall(rf"<w:{kind}Reference[^>]*/>", trailing_sect)
        existing = next((r for r in refs if f'w:type="{hf_type}"' in r), None)
        if existing is None and hf_type == "default":
            existing = next((r for r in refs if 'w:type="' not in r), None)
        rid = re.search(r'r:id="([^"]+)"', existing).group(1) if existing else None
        target = rel_targets.get(rid) if rid else None
        if hf is None and not watermark_only:
            return
        if target:
            import posixpath

            path = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else posixpath.join("word", target)
            )
            try:
                original_xml = zf.read(path).decode("utf-8")
            except KeyError:
                original_xml = None
            if watermark_only:
                if kind != "header" or original_xml is None:
                    return
                part_xml = _patch_header_watermark(original_xml, watermark)
            else:
                if hf is None:
                    return
                part_xml = _header_footer_part_xml(kind, hf, watermark, original_xml)
            hf_parts.append({"path": path, "xml": part_xml})
        else:
            if hf is None and not (watermark_only and watermark and kind == "header"):
                return
            if hf is None:
                hf = {"text": ""}
            part_xml = _header_footer_part_xml(kind, hf, watermark)
            n = 1
            while f"word/{kind}{n}.xml" in zf.namelist() or any(
                p["path"] == f"word/{kind}{n}.xml" for p in hf_parts
            ):
                n += 1
            filename = f"{kind}{n}.xml"
            nonlocal_holder[0] += 1
            new_r_id = f"rId{nonlocal_holder[0]}"
            new_rels.append(
                {"rId": new_r_id, "type": HF_REL_TYPE[kind], "target": filename, "external": False}
            )
            hf_parts.append({"path": f"word/{filename}", "xml": part_xml})
            hf_ref_tags.append(f'<w:{kind}Reference w:type="{hf_type}" r:id="{new_r_id}"/>')
            hf_overrides.append(
                f'<Override PartName="/word/{filename}" '
                f'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>'
            )

    effective_watermark = (
        options["watermark"] if options.get("watermark") is not None else parsed.watermark_text
    )
    if options.get("header") is not None:
        plan_header_footer("header", options["header"], effective_watermark, "default", False)
    elif options.get("watermark") is not None:
        plan_header_footer("header", None, effective_watermark, "default", True)
    if options.get("footer") is not None:
        plan_header_footer("footer", options["footer"])
    for key, kind, t in (
        ("headerFirst", "header", "first"),
        ("footerFirst", "footer", "first"),
        ("headerEven", "header", "even"),
        ("footerEven", "footer", "even"),
    ):
        if options.get(key) is not None:
            plan_header_footer(kind, options[key], None, t)

    numbering_path = "word/numbering.xml"
    numbering_out = None
    numbering_is_new = False
    if (options.get("numbering") or {}).get("newDefs") or (options.get("numbering") or {}).get(
        "restartNums"
    ):
        try:
            xml = zf.read(numbering_path).decode("utf-8")
        except KeyError:
            xml = None
        if xml is None:
            xml = BLANK_NUMBERING_XML
            numbering_is_new = True
            nonlocal_holder[0] += 1
            new_rels.append(
                {
                    "rId": f"rId{nonlocal_holder[0]}",
                    "type": NUMBERING_REL_TYPE,
                    "target": "numbering.xml",
                    "external": False,
                }
            )
        max_abs = -1
        for m in re.finditer(r'<w:abstractNum [^>]*w:abstractNumId="(\d+)"', xml):
            max_abs = max(max_abs, int(m.group(1)))
        abs_xmls, num_xmls = [], []
        for d in (options.get("numbering") or {}).get("newDefs") or []:
            max_abs += 1
            try:
                num_id = int(d["numId"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("numbering numId must be a non-negative integer") from exc
            if num_id < 0:
                raise ValueError("numbering numId must be a non-negative integer")
            kind = str(d.get("kind", "ordered"))
            if kind not in {"bullet", "ordered", "decimal"}:
                raise ValueError("numbering kind must be bullet or ordered")
            abs_xmls.append(abstract_num_xml(str(max_abs), kind, d.get("levels")))
            num_xmls.append(
                f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{max_abs}"/></w:num>'
            )
        for r in (options.get("numbering") or {}).get("restartNums") or []:
            try:
                num_id = int(r["numId"])
                abstract_id = int(r["abstractNumId"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "restartNums numId/abstractNumId must be non-negative integers"
                ) from exc
            if num_id < 0 or abstract_id < 0:
                raise ValueError("restartNums numId/abstractNumId must be non-negative integers")
            override_parts = []
            for ilvl, value in (r.get("startOverrides") or {}).items():
                try:
                    ilvl_i = int(ilvl)
                    start_i = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("numbering override levels/starts must be integers") from exc
                if not 0 <= ilvl_i <= 8 or start_i < 0:
                    raise ValueError("numbering override level must be 0..8 and start non-negative")
                override_parts.append(
                    f'<w:lvlOverride w:ilvl="{ilvl_i}"><w:startOverride w:val="{start_i}"/></w:lvlOverride>'
                )
            overrides = "".join(override_parts)
            num_xmls.append(
                f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/>{overrides}</w:num>'
            )
        if abs_xmls:
            if re.search(r"<w:num[\s>]", xml):
                xml = re.sub(r"<w:num[\s>]", "".join(abs_xmls) + r"\g<0>", xml, count=1)
            else:
                xml = xml.replace("</w:numbering>", "".join(abs_xmls) + "</w:numbering>", 1)
        xml = xml.replace("</w:numbering>", "".join(num_xmls) + "</w:numbering>", 1)
        numbering_out = xml

    styles_path = "word/styles.xml"
    styles_out = None
    if options.get("styleUpserts"):
        try:
            xml = zf.read(styles_path).decode("utf-8")
        except KeyError:
            xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"></w:styles>'
            )
        for up in options["styleUpserts"]:
            style_xml = _build_style_xml(up)
            existing = re.compile(
                rf'<w:style [^>]*w:styleId="{re.escape(up["styleId"])}"[\s\S]*?</w:style>'
            )
            if existing.search(xml):
                xml = existing.sub(lambda m, _style_xml=style_xml: _style_xml, xml, count=1)
            else:
                xml = xml.replace("</w:styles>", style_xml + "</w:styles>", 1)
        styles_out = xml

    comments_path = "word/comments.xml"
    comments_ext_path = "word/commentsExtended.xml"
    comments_xml = comments_ext_xml = None
    comments_is_new = comments_ext_is_new = False
    if options.get("comments") is not None:
        seq = [1]
        with_ids = []
        for c in options["comments"]:
            if isinstance(c, dict):
                c = CommentInfo(
                    id=str(c.get("id", "")),
                    author=str(c.get("author", "")),
                    text=str(c.get("text", "")),
                    initials=c.get("initials"),
                    date=c.get("date"),
                    parent_id=c.get("parentId", c.get("parent_id")),
                    done=bool(c.get("done", False)),
                    para_id=c.get("paraId", c.get("para_id")),
                )
            if not getattr(c, "para_id", None):
                try:
                    numeric_id = int(c.id)
                except (TypeError, ValueError) as exc:
                    raise ValueError("comment id must be an integer") from exc
                c.para_id = format(0x10000000 + seq[0] * 0x1111 + numeric_id, "08X")
                seq[0] += 1
            with_ids.append(c)
        try:
            orig = zf.read(comments_path).decode("utf-8")
        except KeyError:
            orig = None
        comments_xml = _build_comments_xml(with_ids, orig)
        if comments_path not in zf.namelist():
            comments_is_new = True
            nonlocal_holder[0] += 1
            new_rels.append(
                {
                    "rId": f"rId{nonlocal_holder[0]}",
                    "type": COMMENTS_REL_TYPE,
                    "target": "comments.xml",
                    "external": False,
                }
            )
        need_ext = comments_ext_path in zf.namelist() or any(
            getattr(c, "parent_id", None) is not None or getattr(c, "done", False) for c in with_ids
        )
        if need_ext:
            comments_ext_xml = _build_comments_extended(with_ids)
            if comments_ext_path not in zf.namelist():
                comments_ext_is_new = True
                nonlocal_holder[0] += 1
                new_rels.append(
                    {
                        "rId": f"rId{nonlocal_holder[0]}",
                        "type": COMMENTS_EXT_REL_TYPE,
                        "target": "commentsExtended.xml",
                        "external": False,
                    }
                )

    notes_parts = []

    def plan_notes(kind, notes):
        if notes is None:
            return
        notes = [
            n
            if isinstance(n, NoteInfo)
            else NoteInfo(
                id=str(n.get("id", "")),
                text=str(n.get("text", "")),
                rich_paras=n.get("richParas", n.get("rich_paras")),
            )
            for n in notes
        ]
        path = NOTE_PART_PATH[kind]
        try:
            original_xml = zf.read(path).decode("utf-8")
        except KeyError:
            original_xml = None
        notes_parts.append(
            {
                "path": path,
                "xml": build_notes_xml(kind, notes, original_xml),
                "isNew": path not in zf.namelist(),
                "kind": kind,
            }
        )
        if path not in zf.namelist():
            nonlocal_holder[0] += 1
            new_rels.append(
                {
                    "rId": f"rId{nonlocal_holder[0]}",
                    "type": NOTE_REL_TYPE[kind],
                    "target": path.replace("word/", "", 1),
                    "external": False,
                }
            )

    plan_notes("footnote", options.get("footnotes"))
    plan_notes("endnote", options.get("endnotes"))

    sources_part = None
    if options.get("sources") is not None:
        source_models = [
            s
            if isinstance(s, SourceInfo)
            else SourceInfo(
                tag=str(s.get("tag", "")),
                type=str(s.get("type", "Misc")),
                author=str(s.get("author", "")),
                title=str(s.get("title", "")),
                year=str(s.get("year", "")),
                publisher=s.get("publisher"),
                url=s.get("url"),
            )
            for s in options["sources"]
        ]
        existing = None
        for name in zf.namelist():
            if re.fullmatch(r"customXml/item\d+\.xml", name):
                xml = zf.read(name).decode("utf-8")
                if "Sources" in xml:
                    existing = name
                    break
        xml = build_sources_xml(source_models, zf.read(existing).decode() if existing else None)
        if existing:
            n = re.search(r"item(\d+)\.xml$", existing).group(1)
            sources_part = {
                "path": existing,
                "propsPath": f"customXml/itemProps{n}.xml",
                "xml": xml,
                "isNew": False,
            }
        else:
            n = 1
            while f"customXml/item{n}.xml" in zf.namelist():
                n += 1
            sources_part = {
                "path": f"customXml/item{n}.xml",
                "propsPath": f"customXml/itemProps{n}.xml",
                "xml": xml,
                "isNew": True,
            }
            nonlocal_holder[0] += 1
            new_rels.append(
                {
                    "rId": f"rId{nonlocal_holder[0]}",
                    "type": CUSTOM_XML_REL_TYPE,
                    "target": f"../customXml/item{n}.xml",
                    "external": False,
                }
            )

    theme_part = None
    if options.get("themeFonts") or options.get("themeColors"):
        try:
            xml = zf.read(THEME_PART_PATH).decode("utf-8")
            if options.get("themeFonts"):
                xml = apply_theme_fonts(xml, options["themeFonts"])
            if options.get("themeColors"):
                xml = apply_theme_colors(xml, options["themeColors"])
            theme_part = {"xml": xml, "isNew": False}
        except KeyError:
            theme_part = {
                "xml": build_theme_xml(
                    options.get("themeFonts")
                    or {"major": "Times New Roman", "minor": "Times New Roman", "eastAsia": ""},
                    options.get("themeColors") or {},
                ),
                "isNew": True,
            }
            nonlocal_holder[0] += 1
            new_rels.append(
                {
                    "rId": f"rId{nonlocal_holder[0]}",
                    "type": THEME_REL_TYPE,
                    "target": "theme/theme1.xml",
                    "external": False,
                }
            )

    parts = []
    for i, fb in enumerate(final_blocks):
        if fb["kind"] == "original":
            el = elements[fb["docxIndex"]]
            xml = document_xml[el["start"] : el["end"]]
        elif fb["kind"] == "generated":
            blk = fb["block"]
            if not isinstance(blk, GeneratedBlock):
                blk = _coerce_generated_block(blk)
            xml = generate_paragraph_xml(blk, gen_ctx)
            if blk.sdt_shell:
                xml = blk.sdt_shell["openXml"] + xml + blk.sdt_shell["closeXml"]
        elif fb["kind"] == "xml":
            xml = fb["xml"]
        elif fb["kind"] == "chart":
            xml = embed_chart(fb["chart"])
        elif fb["kind"] == "image":
            xml = embed_image(fb["image"])
        else:
            raise ValueError(f"unknown block kind: {fb.get('kind')}")
        if options.get("inks") is not None:
            xml = strip_ink_runs(xml)
        block_inks = inks_by_block.get(i)
        if block_inks and re.match(r"<w:p[\s/>]", xml):
            injected = inject_ink_runs_into_paragraph(
                xml, "".join(ink_run_xml(k) for k in block_inks)
            )
            if injected is not None:
                xml = injected
        if fb.get("revision"):
            rev = fb["revision"]
            rev_kind = rev.get("kind")
            if rev_kind not in ("ins", "del", "moveFrom", "moveTo"):
                raise ValueError("revision kind must be ins, del, moveFrom, or moveTo")
            if not re.match(rf"^<w:{rev_kind}[\s>]", xml):
                attrs = (
                    f' w:id="{escape_xml_attr(rev.get("id") or "0")}"'
                    f' w:author="{escape_xml_attr(rev.get("author") or "")}"'
                    + (f' w:date="{escape_xml_attr(rev["date"])}"' if rev.get("date") else "")
                )
                xml = f"<w:{rev_kind}{attrs}>{xml}</w:{rev_kind}>"
        parts.append(xml)

    for block in parsed.blocks:
        if block.hidden and block.docx_index is not None:
            el = elements[block.docx_index]
            xml = document_xml[el["start"] : el["end"]]
            if "<w:sectPr" in xml:
                if options.get("section"):
                    xml = apply_section_settings(xml, options["section"])
                if options.get("sectionStartType"):
                    xml = apply_section_start_type(xml, options["sectionStartType"])
                if options.get("pgNumType") is not None:
                    xml = apply_page_num_type(
                        xml, options["pgNumType"].get("fmt"), options["pgNumType"].get("start")
                    )
                if options.get("titlePg") is not None:
                    xml = _apply_title_pg(xml, options["titlePg"])
                if hf_ref_tags:
                    xml = re.sub(r"(<w:sectPr[^>]*>)", r"\1" + "".join(hf_ref_tags), xml, count=1)
            parts.append(xml)

    new_document_xml = document_xml[:body_start] + "".join(parts) + document_xml[body_end:]
    if "<m:" in new_document_xml and not re.search(r"<w:document[^>]*xmlns:m=", new_document_xml):
        new_document_xml = new_document_xml.replace(
            "<w:document ",
            '<w:document xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ',
            1,
        )
    if options.get("pageColor") is not None:
        new_document_xml = _apply_page_color(new_document_xml, options["pageColor"])

    settings_path = "word/settings.xml"
    settings_xml = None
    settings_is_new = False
    if (
        options.get("pageColor")
        or options.get("protection") is not None
        or options.get("evenAndOddHeaders") is not None
    ):
        try:
            xml = zf.read(settings_path).decode("utf-8")
        except KeyError:
            xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"></w:settings>'
            )
            settings_is_new = True
            nonlocal_holder[0] += 1
            new_rels.append(
                {
                    "rId": f"rId{nonlocal_holder[0]}",
                    "type": SETTINGS_REL_TYPE,
                    "target": "settings.xml",
                    "external": False,
                }
            )
        touched = False
        if options.get("pageColor") and "<w:displayBackgroundShape" not in xml:
            xml = re.sub(r"(<w:settings[^>]*>)", r"\1<w:displayBackgroundShape/>", xml, count=1)
            touched = True
        if options.get("protection") is not None:
            xml = _apply_protection(xml, options["protection"])
            touched = True
        if options.get("evenAndOddHeaders") is not None:
            xml = _apply_even_odd(xml, options["evenAndOddHeaders"])
            touched = True
        if touched:
            settings_xml = xml

    rels_changed = False
    if options.get("inks") is not None and rels_xml:
        cleaned = INK_REL_RE.sub("", rels_xml)
        if cleaned != rels_xml:
            rels_xml, rels_changed = cleaned, True
    if new_rels:
        inserts = "".join(
            f'<Relationship Id="{escape_xml_attr(r["rId"])}" Type="{r["type"]}" Target="{escape_xml_attr(r["target"])}"'
            + (' TargetMode="External"' if r["external"] else "")
            + "/>"
            for r in new_rels
        )
        rels_xml = rels_xml.replace("</Relationships>", inserts + "</Relationships>", 1)
        rels_changed = True

    content_types_path = "[Content_Types].xml"
    content_types_xml = None
    has_new_parts = (
        len(used_ext) > 0
        or len(hf_overrides) > 0
        or len(new_chart_parts) > 0
        or len(new_chart_wbs) > 0
        or settings_is_new
        or comments_is_new
        or comments_ext_is_new
        or numbering_is_new
        or any(p["isNew"] for p in notes_parts)
        or (sources_part or {}).get("isNew")
        or (theme_part or {}).get("isNew")
    )
    if has_new_parts and content_types_path in zf.namelist():
        content_types_xml = zf.read(content_types_path).decode("utf-8")

        def add_override(part_name, content_type):
            nonlocal content_types_xml
            if f'PartName="{part_name}"' not in content_types_xml:
                content_types_xml = content_types_xml.replace(
                    "</Types>",
                    f'<Override PartName="{part_name}" ContentType="{content_type}"/></Types>',
                    1,
                )

        for ext in used_ext:
            if not re.search(rf'Extension="{ext}"', content_types_xml):
                mime = IMAGE_CONTENT_TYPE.get(ext)
                if not mime:
                    raise ValueError(f"no content type registered for image extension: {ext}")
                content_types_xml = content_types_xml.replace(
                    "</Types>", f'<Default Extension="{ext}" ContentType="{mime}"/></Types>', 1
                )
        for override in hf_overrides:
            pn = re.search(r'PartName="([^"]+)"', override).group(1)
            if f'PartName="{pn}"' not in content_types_xml:
                content_types_xml = content_types_xml.replace("</Types>", override + "</Types>", 1)
        if comments_is_new:
            add_override(
                "/word/comments.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
            )
        if comments_ext_is_new:
            add_override(
                "/word/commentsExtended.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml",
            )
        if settings_is_new:
            add_override(
                "/word/settings.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
            )
        if numbering_is_new:
            add_override(
                "/word/numbering.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
            )
        for part in new_chart_parts:
            add_override("/" + part["path"], CHART_CONTENT_TYPE)
        for wb in new_chart_wbs:
            add_override("/" + wb["xlsxPath"], XLSX_CONTENT_TYPE)
        for part in notes_parts:
            if part["isNew"]:
                add_override("/" + part["path"], NOTE_CONTENT_TYPE[part["kind"]])
        if sources_part and sources_part["isNew"]:
            add_override(
                "/" + sources_part["propsPath"],
                "application/vnd.openxmlformats-officedocument.customXmlProperties+xml",
            )
        if theme_part and theme_part["isNew"]:
            add_override("/" + THEME_PART_PATH, THEME_CONTENT_TYPE)

    core_out = None
    if CORE_PROPS_PATH in zf.namelist():
        core_out = _patch_core_props(
            zf.read(CORE_PROPS_PATH).decode("utf-8"), options.get("savedAt")
        )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as oz:
        for name in zf.namelist():
            if options.get("inks") is not None and INK_MEDIA_PATH_RE.match(name):
                continue
            hf_part = next((p for p in hf_parts if p["path"] == name), None)
            if name == "word/document.xml":
                oz.writestr(name, new_document_xml)
            elif hf_part:
                oz.writestr(name, hf_part["xml"])
            elif name == rels_path and rels_changed:
                oz.writestr(name, rels_xml)
            elif name == content_types_path and content_types_xml is not None:
                oz.writestr(name, content_types_xml)
            elif name == settings_path and settings_xml is not None:
                oz.writestr(name, settings_xml)
            elif name == comments_path and comments_xml is not None:
                oz.writestr(name, comments_xml)
            elif name == comments_ext_path and comments_ext_xml is not None:
                oz.writestr(name, comments_ext_xml)
            elif name == numbering_path and numbering_out is not None:
                oz.writestr(name, numbering_out)
            elif name == styles_path and styles_out is not None:
                oz.writestr(name, styles_out)
            elif any(p["path"] == name for p in notes_parts):
                oz.writestr(name, next(p["xml"] for p in notes_parts if p["path"] == name))
            elif sources_part and name == sources_part["path"]:
                oz.writestr(name, sources_part["xml"])
            elif theme_part and name == THEME_PART_PATH:
                oz.writestr(name, theme_part["xml"])
            elif name == CORE_PROPS_PATH and core_out is not None:
                oz.writestr(name, core_out)
            elif options.get("partXml") and name in options["partXml"]:
                oz.writestr(name, options["partXml"][name])
            elif options.get("partBinary") and name in options["partBinary"]:
                oz.writestr(name, base64.b64decode(options["partBinary"][name]))
            else:
                oz.writestr(name, zf.read(name))
        for media in new_media:
            oz.writestr(media["path"], base64.b64decode(media["base64"]))
        for part in hf_parts:
            if part["path"] not in zf.namelist():
                oz.writestr(part["path"], part["xml"])
        if rels_changed and rels_path not in zf.namelist():
            oz.writestr(rels_path, rels_xml)
        if comments_is_new and comments_xml is not None:
            oz.writestr(comments_path, comments_xml)
        if comments_ext_is_new and comments_ext_xml is not None:
            oz.writestr(comments_ext_path, comments_ext_xml)
        if numbering_is_new and numbering_out is not None:
            oz.writestr(numbering_path, numbering_out)
        if styles_out is not None and styles_path not in zf.namelist():
            oz.writestr(styles_path, styles_out)
        if settings_is_new and settings_xml is not None:
            oz.writestr(settings_path, settings_xml)
        for part in new_chart_parts:
            oz.writestr(part["path"], part["xml"])
        for wb in new_chart_wbs:
            if wb["relsPath"] not in zf.namelist():
                oz.writestr(wb["relsPath"], wb["relsXml"])
            oz.writestr(wb["xlsxPath"], base64.b64decode(wb["base64"]))
        for part in notes_parts:
            if part["isNew"]:
                oz.writestr(part["path"], part["xml"])
        if sources_part and sources_part["isNew"]:
            oz.writestr(sources_part["path"], sources_part["xml"])
            oz.writestr(sources_part["propsPath"], build_sources_item_props_xml())
            rels_name = sources_part["path"].replace("customXml/", "", 1).replace(".xml", "")
            oz.writestr(
                f"customXml/_rels/{rels_name}.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXmlProps" '
                f'Target="{sources_part["propsPath"].replace("customXml/", "", 1)}"/></Relationships>',
            )
        if theme_part and theme_part["isNew"]:
            oz.writestr(THEME_PART_PATH, theme_part["xml"])
    return out.getvalue()


def _coerce_generated_block(d: dict) -> GeneratedBlock:
    def pick(obj, snake, camel=None, default=None):
        if snake in obj:
            return obj[snake]
        if camel and camel in obj:
            return obj[camel]
        return default

    def revision(v):
        if v is None or isinstance(v, RevisionInfo):
            return v
        return RevisionInfo(
            author=str(v.get("author", "")),
            date=v.get("date"),
            id=str(v["id"]) if v.get("id") is not None else None,
        )

    runs = []
    for r in d.get("runs") or []:
        if isinstance(r, Run):
            runs.append(r)
        else:
            runs.append(
                Run(
                    text=str(r.get("text", "")),
                    raw_r_pr=pick(r, "raw_r_pr", "rawRPr"),
                    style_id=pick(r, "style_id", "styleId"),
                    bold=bool(r.get("bold", False)),
                    italic=bool(r.get("italic", False)),
                    underline=bool(r.get("underline", False)),
                    strike=bool(r.get("strike", False)),
                    color=r.get("color"),
                    size_half_points=pick(r, "size_half_points", "sizeHalfPoints"),
                    font=r.get("font"),
                    char_spacing_twips=pick(r, "char_spacing_twips", "charSpacingTwips"),
                    char_scale_pct=pick(r, "char_scale_pct", "charScalePct"),
                    highlight=r.get("highlight"),
                    vert_align=pick(r, "vert_align", "vertAlign"),
                    em=r.get("em"),
                    link=r.get("link"),
                    comment_ids=pick(r, "comment_ids", "commentIds"),
                    ins=revision(r.get("ins")),
                    del_=revision(pick(r, "del_", "del")),
                    note_ref=pick(r, "note_ref", "noteRef"),
                    xe_term=pick(r, "xe_term", "xeTerm"),
                    ref_field=pick(r, "ref_field", "refField"),
                    instr_field=pick(r, "instr_field", "instrField"),
                    r_pr_change=pick(r, "r_pr_change", "rPrChange"),
                    math=r.get("math"),
                    ruby=r.get("ruby"),
                )
            )
    fmt = d.get("format")
    if isinstance(fmt, dict):
        mapping = {
            "align": "align",
            "line_spacing": "line_spacing",
            "lineSpacing": "line_spacing",
            "line_rule": "line_rule",
            "lineRule": "line_rule",
            "line_raw_twips": "line_raw_twips",
            "lineRawTwips": "line_raw_twips",
            "indent_left": "indent_left",
            "indentLeft": "indent_left",
            "indent_right": "indent_right",
            "indentRight": "indent_right",
            "indent_first_line": "indent_first_line",
            "indentFirstLine": "indent_first_line",
            "space_before": "space_before",
            "spaceBefore": "space_before",
            "space_after": "space_after",
            "spaceAfter": "space_after",
            "page_break_before": "page_break_before",
            "pageBreakBefore": "page_break_before",
            "keep_next": "keep_next",
            "keepNext": "keep_next",
            "keep_lines": "keep_lines",
            "keepLines": "keep_lines",
            "widow_control": "widow_control",
            "widowControl": "widow_control",
            "contextual_spacing": "contextual_spacing",
            "contextualSpacing": "contextual_spacing",
            "shading_fill": "shading_fill",
            "shadingFill": "shading_fill",
            "borders": "borders",
            "tab_stops": "tab_stops",
            "tabStops": "tab_stops",
            "drop_cap": "drop_cap",
            "dropCap": "drop_cap",
            "bidi": "bidi",
        }
        kwargs = {}
        for key, value in fmt.items():
            dest = mapping.get(key)
            if dest is not None and value is not None:
                kwargs[dest] = value
        fmt = ParaFormat(**kwargs)
    return GeneratedBlock(
        type=d.get("type", "paragraph"),
        runs=runs,
        level=d.get("level"),
        style_id=pick(d, "style_id", "styleId"),
        list=d.get("list"),
        format=fmt,
        raw_p_pr=pick(d, "raw_p_pr", "rawPPr"),
        bookmarks=d.get("bookmarks"),
        hidden_bookmarks=pick(d, "hidden_bookmarks", "hiddenBookmarks"),
        comment_starts=pick(d, "comment_starts", "commentStarts"),
        comment_ends=pick(d, "comment_ends", "commentEnds"),
        sdt_shell=pick(d, "sdt_shell", "sdtShell"),
        p_pr_change=pick(d, "p_pr_change", "pPrChange"),
    )


async def find_chart_workbook_path(docx_bytes: bytes, chart_path: str):
    try:
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        d = chart_path.rsplit("/", 1)[0]
        f = chart_path.rsplit("/", 1)[1]
        rels_path = f"{d}/_rels/{f}.rels"
        if rels_path not in zf.namelist():
            return None
        rels_xml = zf.read(rels_path).decode("utf-8")
        m = re.search(r'Type="[^"]*\/package"[^/]*Target="([^"]+)"', rels_xml)
        if not m:
            return None
        import posixpath

        target = m.group(1)
        return posixpath.normpath(
            target[1:] if target.startswith("/") else posixpath.join(d, target)
        )
    except Exception:
        return None


def read_docx_part_base64(docx_bytes: bytes, path: str):
    try:
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        if path not in zf.namelist():
            return None
        return base64.b64encode(zf.read(path)).decode()
    except Exception:
        return None
