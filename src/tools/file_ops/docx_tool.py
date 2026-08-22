"""Native DOCX agent tool backed exclusively by :mod:`docx_engine`.

The public tool intentionally exposes a compact, semantic JSON protocol.  The
agent never edits OOXML/HTML and never has to resend the whole document:
`read` returns compact current-version block ids, while this module applies small block edits and
lets docx_engine preserve every untouched OOXML part byte-for-byte where
possible.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import mimetypes
import struct
from dataclasses import asdict
from pathlib import Path
from typing import Any

from docx_engine import (
    BLANK_BULLET_NUM_ID,
    BLANK_ORDERED_NUM_ID,
    build_blank_docx,
    generate_caption_xml,
    generate_table_model_xml,
    generate_toc_field_xml,
    latex_to_omml,
    parse_docx,
    read_section_settings,
    save_docx,
)
from docx_engine.mathml import math_paragraph_xml
from docx_engine.text_patch import patch_paragraph_texts
from logger import logger
from tools._paths import clean_path, resolve_path
from tools.models import ToolCall, ToolResult

# Compact read representation

_MAX_PREVIEW = 360


def _one_line(value: Any, limit: int = _MAX_PREVIEW) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _block_text(block) -> str:
    if block.runs is not None:
        return "".join(r.text for r in block.runs)
    return block.preview_text or ""


def _table_preview(table: dict | None, max_rows: int = 3, max_cols: int = 6) -> str:
    if not table:
        return ""
    rows = table.get("rows") or []
    rendered: list[str] = []
    for row in rows[:max_rows]:
        vals = []
        for cell in row[:max_cols]:
            vals.append(" / ".join(str(x) for x in (cell.get("paras") or [""])))
        if len(row) > max_cols:
            vals.append("…")
        rendered.append(" | ".join(vals))
    if len(rows) > max_rows:
        rendered.append("…")
    return _one_line(" ; ".join(rendered))


def _format_flags(block) -> str:
    flags: list[str] = []
    fmt = block.format
    if fmt:
        if fmt.align and fmt.align != "left":
            flags.append(f"align={fmt.align}")
        if fmt.page_break_before:
            flags.append("pageBreak")
        if fmt.keep_next:
            flags.append("keepNext")
    runs = block.runs or []
    if runs:
        if any(r.bold for r in runs):
            flags.append("bold")
        if any(r.italic for r in runs):
            flags.append("italic")
        if any(r.underline for r in runs):
            flags.append("underline")
        if any(r.link for r in runs):
            flags.append("link")
        if any(r.note_ref for r in runs):
            flags.append("note")
        if any(r.comment_ids for r in runs):
            flags.append("comment")
        if any(r.math for r in runs):
            flags.append("math")
    return " " + "{" + ",".join(flags) + "}" if flags else ""


def read_docx_compact(path: Path) -> str:
    """Return a low-token, line-addressable view for the generic ``read`` tool."""
    parsed = parse_docx(path.read_bytes())
    visible = [b for b in parsed.blocks if not b.hidden]
    lines = [
        "[DOCX blocks: "
        + str(len(visible))
        + '; edit with docx(action="edit") using current bN ids; '
        'use docx(action="inspect") only when exact formatting/details are needed]'
    ]
    for b in visible:
        if b.type == "heading":
            kind = f"h{b.level or 1}"
            payload = _one_line(_block_text(b))
        elif b.type == "listItem":
            list_kind = (b.list or {}).get("kind") or "list"
            ilvl = (b.list or {}).get("ilvl", 0)
            kind = f"li:{list_kind}:{ilvl}"
            payload = _one_line(_block_text(b))
        elif b.type == "paragraph":
            kind = "p"
            payload = _one_line(_block_text(b))
        elif b.type == "table":
            rows = len((b.table or {}).get("rows") or [])
            cols = max((len(r) for r in ((b.table or {}).get("rows") or [])), default=0)
            kind = f"table:{rows}x{cols}"
            payload = _table_preview(b.table)
        elif b.type == "image":
            kind = "image"
            dims = ""
            if b.image_width_px and b.image_height_px:
                dims = f" {round(b.image_width_px)}x{round(b.image_height_px)}px"
            payload = f"{dims} align={b.image_align or 'left'} wrap={b.image_wrap or 'inline'}".strip()
        elif b.chart_display:
            c = b.chart_display or {}
            kind = "chart"
            payload = _one_line(c.get("title") or c.get("type") or "chart")
        elif b.formula_display:
            kind = "math"
            payload = _one_line(
                b.preview_text or "".join((b.formula_display or {}).get("tokens") or [])
            )
        else:
            kind = b.type or "object"
            payload = _one_line(b.label or b.preview_text or "")
        lines.append(f"{b.id} {kind}{_format_flags(b)} | {payload}")

    meta: list[str] = []
    if parsed.header_text:
        meta.append("header")
    if parsed.footer_text:
        meta.append("footer")
    if parsed.comments:
        meta.append(f"comments={len(parsed.comments)}")
    if parsed.footnotes:
        meta.append(f"footnotes={len(parsed.footnotes)}")
    if parsed.endnotes:
        meta.append(f"endnotes={len(parsed.endnotes)}")
    if parsed.watermark_text:
        meta.append("watermark")
    if parsed.protection:
        meta.append("protected")
    if meta:
        lines.append("meta | " + ", ".join(meta))
    return "\n".join(lines)


# JSON helpers


def _revision_json(v):
    if v is None:
        return None
    return {
        k: val
        for k, val in {"author": v.author, "date": v.date, "id": v.id}.items()
        if val is not None
    }


def _format_json(f) -> dict | None:
    if f is None:
        return None
    mapping = {
        "align": "align",
        "line_spacing": "lineSpacing",
        "line_rule": "lineRule",
        "line_raw_twips": "lineRawTwips",
        "indent_left": "indentLeft",
        "indent_right": "indentRight",
        "indent_first_line": "indentFirstLine",
        "space_before": "spaceBefore",
        "space_after": "spaceAfter",
        "page_break_before": "pageBreakBefore",
        "keep_next": "keepNext",
        "keep_lines": "keepLines",
        "widow_control": "widowControl",
        "contextual_spacing": "contextualSpacing",
        "shading_fill": "shadingFill",
        "borders": "borders",
        "tab_stops": "tabStops",
        "drop_cap": "dropCap",
        "bidi": "bidi",
    }
    out = {}
    for attr, key in mapping.items():
        value = getattr(f, attr)
        if value is not None:
            out[key] = value
    return out


def _run_json(r, *, include_raw: bool = False) -> dict:
    d: dict[str, Any] = {"text": r.text}
    fields = (
        ("style_id", "styleId"),
        ("bold", "bold"),
        ("italic", "italic"),
        ("underline", "underline"),
        ("strike", "strike"),
        ("color", "color"),
        ("size_half_points", "sizeHalfPoints"),
        ("font", "font"),
        ("char_spacing_twips", "charSpacingTwips"),
        ("char_scale_pct", "charScalePct"),
        ("highlight", "highlight"),
        ("vert_align", "vertAlign"),
        ("em", "em"),
        ("link", "link"),
        ("comment_ids", "commentIds"),
        ("note_ref", "noteRef"),
        ("xe_term", "xeTerm"),
        ("ref_field", "refField"),
        ("instr_field", "instrField"),
        ("r_pr_change", "rPrChange"),
        ("math", "math"),
        ("ruby", "ruby"),
    )
    for attr, key in fields:
        value = getattr(r, attr)
        if value is not None and value is not False:
            d[key] = value
    if include_raw and r.raw_r_pr is not None:
        d["rawRPr"] = r.raw_r_pr
    if r.ins is not None:
        d["ins"] = _revision_json(r.ins)
    if r.del_ is not None:
        d["del"] = _revision_json(r.del_)
    return d


def _block_json(b, *, include_raw: bool = False, include_media: bool = False) -> dict:
    d: dict[str, Any] = {"id": b.id, "type": b.type, "docxIndex": b.docx_index}
    for attr, key in (
        ("level", "level"),
        ("style_id", "styleId"),
        ("list", "list"),
        ("label", "label"),
        ("preview_text", "previewText"),
        ("image_width_px", "imageWidthPx"),
        ("image_height_px", "imageHeightPx"),
        ("image_align", "imageAlign"),
        ("image_wrap", "imageWrap"),
        ("table", "table"),
        ("field_display", "fieldDisplay"),
        ("bookmarks", "bookmarks"),
        ("hidden_bookmarks", "hiddenBookmarks"),
        ("comment_starts", "commentStarts"),
        ("comment_ends", "commentEnds"),
        ("move_revision", "moveRevision"),
        ("p_pr_change_info", "pPrChangeInfo"),
        ("block_revision", "blockRevision"),
        ("chart_display", "chartDisplay"),
        ("formula_display", "formulaDisplay"),
    ):
        value = getattr(b, attr)
        if value is not None:
            d[key] = value
    if b.format is not None:
        d["format"] = _format_json(b.format)
    if b.runs is not None:
        d["text"] = "".join(r.text for r in b.runs)
        d["runs"] = [_run_json(r, include_raw=include_raw) for r in b.runs]
    if include_raw:
        d["rawPPr"] = b.raw_p_pr
        d["originalXml"] = b.original_xml
    if include_media and b.image_data_url:
        d["imageDataUrl"] = b.image_data_url
    return d


def _doc_meta_json(parsed) -> dict:
    return {
        "headerText": parsed.header_text,
        "footerText": parsed.footer_text,
        "footerHasPageNumber": parsed.footer_has_page_number,
        "watermarkText": parsed.watermark_text,
        "titlePg": parsed.title_pg,
        "evenAndOddHeaders": parsed.even_and_odd_headers,
        "comments": [asdict(x) for x in parsed.comments],
        "footnotes": [asdict(x) for x in parsed.footnotes],
        "endnotes": [asdict(x) for x in parsed.endnotes],
        "sources": [asdict(x) for x in parsed.sources],
        "protection": parsed.protection,
        "themeFonts": parsed.theme_fonts,
        "themeColors": parsed.theme_colors,
        "section": read_section_settings(parsed),
        "inkCount": len(parsed.inks or []),
    }


# Friendly block DSL -> engine blocks


def _normalize_run(run: Any) -> dict:
    if isinstance(run, str):
        return {"text": run}
    if not isinstance(run, dict):
        raise ValueError("run must be a string or object")
    out = dict(run)
    # Agent-friendly aliases.
    if "size" in out and "sizeHalfPoints" not in out and "size_half_points" not in out:
        try:
            size = float(out.pop("size"))
        except (TypeError, ValueError) as exc:
            raise ValueError("run.size must be numeric points") from exc
        if not math.isfinite(size) or size <= 0:
            raise ValueError("run.size must be a finite positive number")
        out["sizeHalfPoints"] = max(1, round(size * 2))
    if "superscript" in out and out.pop("superscript"):
        out["vertAlign"] = "superscript"
    if "subscript" in out and out.pop("subscript"):
        out["vertAlign"] = "subscript"
    link = out.get("link")
    if isinstance(link, str):
        out["link"] = {"href": link}
    if "latex" in out:
        latex = str(out.pop("latex"))
        out["text"] = ""
        out["math"] = {"omml": f"<m:oMath>{latex_to_omml(latex)}</m:oMath>"}
    out.setdefault("text", "")
    return out


def _runs_from_spec(spec: dict) -> list[dict]:
    if spec.get("runs") is not None:
        runs = spec["runs"]
        if not isinstance(runs, list):
            raise ValueError("block.runs must be an array")
        return [_normalize_run(r) for r in runs]
    return [{"text": str(spec.get("text", ""))}]


def _num_kind(parsed, num_id: str) -> str | None:
    info = (parsed.numbering or {}).get(str(num_id)) or {}
    levels = info.get("levels") or {}
    fmts = {str(v.get("numFmt", "")) for v in levels.values()}
    if "bullet" in fmts:
        return "bullet"
    if fmts:
        return "ordered"
    return None


def _ensure_num_id(parsed, kind: str, options: dict) -> str:
    kind = "bullet" if kind in ("bullet", "unordered") else "ordered"
    for num_id in parsed.numbering or {}:
        if _num_kind(parsed, str(num_id)) == kind:
            return str(num_id)
    # build_blank_docx already contains these ids. This branch mainly serves
    # third-party documents that have no numbering part at all.
    preferred = str(BLANK_BULLET_NUM_ID if kind == "bullet" else BLANK_ORDERED_NUM_ID)
    used = {int(x) for x in (parsed.numbering or {}) if str(x).isdigit()}
    num_id = int(preferred)
    if num_id in used:
        num_id = max(used | {0}) + 1
    numbering = options.setdefault("numbering", {})
    defs = numbering.setdefault("newDefs", [])
    if not any(str(d.get("numId")) == str(num_id) for d in defs):
        defs.append({"numId": num_id, "kind": kind})
    return str(num_id)


def _simple_table_model(spec: dict) -> dict:
    rows = spec.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("table.rows must be a non-empty array")
    header = bool(spec.get("header", spec.get("headerRow", False)))
    model_rows = []
    for ri, row in enumerate(rows):
        if not isinstance(row, list):
            raise ValueError("each table row must be an array")
        cells = []
        for raw in row:
            if isinstance(raw, dict):
                cell = dict(raw)
                if "text" in cell and "paras" not in cell:
                    cell["paras"] = [str(cell.pop("text"))]
            else:
                cell = {"paras": [str(raw)]}
            if header and ri == 0:
                cell.setdefault("bold", True)
                cell.setdefault("fill", "D9EAF7")
            cells.append(cell)
        model_rows.append(cells)
    model: dict[str, Any] = {"rows": model_rows}
    if spec.get("colWidthsPct") is not None:
        model["colWidthsPct"] = spec["colWidthsPct"]
    if spec.get("colWidthsTwips") is not None:
        model["colWidthsTwips"] = spec["colWidthsTwips"]
    return model


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(data):
            break
        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            h = int.from_bytes(data[i + 3 : i + 5], "big")
            w = int.from_bytes(data[i + 5 : i + 7], "big")
            return w, h
        i += length
    return None


def _image_size(path: Path, data: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        pass
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"BM") and len(data) >= 26:
        return abs(int.from_bytes(data[18:22], "little", signed=True)), abs(
            int.from_bytes(data[22:26], "little", signed=True)
        )
    return _jpeg_size(data)


def _image_block(spec: dict) -> dict:
    raw_path = clean_path(spec.get("path", ""))
    if not raw_path:
        raise ValueError("image block requires path")
    p = resolve_path(raw_path)
    if not p.is_file():
        raise ValueError(f"image file not found: {raw_path}")
    data = p.read_bytes()
    mime = str(spec.get("mime") or mimetypes.guess_type(p.name)[0] or "").lower()
    if mime == "image/svg":
        mime = "image/svg+xml"
    if not mime.startswith("image/"):
        raise ValueError(f"cannot determine image MIME type: {p.name}")
    intrinsic = _image_size(p, data)
    width = spec.get("widthPx")
    height = spec.get("heightPx")
    try:
        width = float(width) if width is not None else None
        height = float(height) if height is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError("image widthPx/heightPx must be numeric") from exc
    if intrinsic:
        iw, ih = intrinsic
        if width is None and height is None:
            scale = min(1.0, 640.0 / max(iw, 1))
            width, height = iw * scale, ih * scale
        elif width is None:
            width = iw * float(height) / max(ih, 1)
        elif height is None:
            height = ih * float(width) / max(iw, 1)
    if width is None or height is None:
        raise ValueError("image dimensions unavailable; provide widthPx and heightPx")
    return {
        "kind": "image",
        "image": {
            "mime": mime,
            "base64": base64.b64encode(data).decode("ascii"),
            "widthPx": width,
            "heightPx": height,
            **({"align": spec["align"]} if spec.get("align") else {}),
            **({"wrap": spec["wrap"]} if spec.get("wrap") else {}),
        },
    }


def _block_spec_to_final(spec: Any, parsed, options: dict) -> list[dict]:
    if isinstance(spec, str):
        spec = {"type": "p", "text": spec}
    if not isinstance(spec, dict):
        raise ValueError("block must be a string or object")
    typ = str(spec.get("type", "p")).strip()
    low = typ.lower()

    if low in ("p", "paragraph"):
        return [
            {
                "kind": "generated",
                "block": {
                    "type": "paragraph",
                    "runs": _runs_from_spec(spec),
                    **({"styleId": spec["styleId"]} if spec.get("styleId") else {}),
                    **(
                        {"format": spec["format"]}
                        if spec.get("format") is not None
                        else {}
                    ),
                },
            }
        ]
    if low in ("h", "heading") or (low.startswith("h") and low[1:].isdigit()):
        level = int(
            spec.get("level")
            or (low[1:] if low.startswith("h") and low[1:].isdigit() else 1)
        )
        return [
            {
                "kind": "generated",
                "block": {
                    "type": "heading",
                    "level": level,
                    "runs": _runs_from_spec(spec),
                    **({"styleId": spec["styleId"]} if spec.get("styleId") else {}),
                    **(
                        {"format": spec["format"]}
                        if spec.get("format") is not None
                        else {}
                    ),
                },
            }
        ]
    if low in ("li", "list", "listitem"):
        list_spec = spec.get("list")
        if isinstance(list_spec, dict):
            ls = dict(list_spec)
            list_kind = str(ls.get("kind") or "bullet")
            num_id = str(ls.get("numId") or _ensure_num_id(parsed, list_kind, options))
            ilvl = int(ls.get("ilvl", spec.get("level", 0)) or 0)
        else:
            list_kind = str(list_spec or spec.get("kind") or "bullet")
            num_id = _ensure_num_id(parsed, list_kind, options)
            ilvl = int(spec.get("level", 0) or 0)
        return [
            {
                "kind": "generated",
                "block": {
                    "type": "listItem",
                    "runs": _runs_from_spec(spec),
                    "list": {
                        "kind": "bullet"
                        if list_kind in ("bullet", "unordered")
                        else "ordered",
                        "numId": num_id,
                        "ilvl": max(0, min(8, ilvl)),
                    },
                    **(
                        {"format": spec["format"]}
                        if spec.get("format") is not None
                        else {}
                    ),
                },
            }
        ]
    if low == "table":
        model = _simple_table_model(spec)
        return [{"kind": "xml", "xml": generate_table_model_xml(model)}]
    if low in ("math", "equation"):
        latex = str(spec.get("latex", ""))
        if not latex:
            raise ValueError("math block requires latex")
        return [
            {
                "kind": "xml",
                "xml": math_paragraph_xml(
                    latex_to_omml(latex), str(spec.get("align") or "center")
                ),
            }
        ]
    if low == "image":
        return [_image_block(spec)]
    if low == "chart":
        chart = (
            spec.get("chart")
            if isinstance(spec.get("chart"), dict)
            else {k: v for k, v in spec.items() if k != "type"}
        )
        if not chart.get("categories") or not chart.get("series"):
            raise ValueError("chart requires categories and series")
        return [{"kind": "chart", "chart": chart}]
    if low in ("pagebreak", "page_break"):
        return [{"kind": "xml", "xml": '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'}]
    if low == "toc":
        entries = spec.get("entries") or []
        return [{"kind": "xml", "xml": x} for x in generate_toc_field_xml(entries)]
    if low == "caption":
        return [
            {
                "kind": "xml",
                "xml": generate_caption_xml(
                    str(spec.get("label") or "Figure"),
                    int(spec.get("number") or 1),
                    str(spec.get("text") or ""),
                ),
            }
        ]
    raise ValueError(f"unsupported DOCX block type: {typ}")


def _blocks_to_final(blocks: Any, parsed, options: dict) -> list[dict]:
    if blocks is None:
        return []
    if not isinstance(blocks, list):
        raise ValueError("blocks must be an array")
    out: list[dict] = []
    for b in blocks:
        out.extend(_block_spec_to_final(b, parsed, options))
    return out


def _original_items(parsed) -> list[dict]:
    return [
        {
            "sourceId": b.id,
            "sourceBlock": b,
            "final": {"kind": "original", "docxIndex": b.docx_index},
        }
        for b in parsed.blocks
        if not b.hidden and b.docx_index is not None
    ]


def _find_item_indexes(items: list[dict], target: Any) -> list[int]:
    targets = target if isinstance(target, list) else [target]
    indexes: list[int] = []
    for raw in targets:
        token = str(raw)
        found = None
        for i, item in enumerate(items):
            if item.get("sourceId") == token:
                found = i
                break
            block = item.get("sourceBlock")
            if block is not None and token.isdigit() and block.docx_index == int(token):
                found = i
                break
        if found is None:
            raise ValueError(
                f"DOCX target not found: {raw!r}; re-read the document for current block ids"
            )
        if found not in indexes:
            indexes.append(found)
    return indexes


def _generated_from_source(block) -> dict:
    if block.type not in ("paragraph", "heading", "listItem"):
        raise ValueError(
            f"set is supported only for text blocks, got {block.type}; use replace instead"
        )
    return {
        "type": block.type,
        "runs": [_run_json(r, include_raw=True) for r in (block.runs or [])],
        **({"level": block.level} if block.level is not None else {}),
        **({"styleId": block.style_id} if block.style_id is not None else {}),
        **({"list": copy.deepcopy(block.list)} if block.list is not None else {}),
        **({"format": _format_json(block.format)} if block.format is not None else {}),
        **({"rawPPr": block.raw_p_pr} if block.raw_p_pr is not None else {}),
        **({"bookmarks": block.bookmarks} if block.bookmarks is not None else {}),
        **(
            {"hiddenBookmarks": block.hidden_bookmarks}
            if block.hidden_bookmarks is not None
            else {}
        ),
        **(
            {"commentStarts": block.comment_starts}
            if block.comment_starts is not None
            else {}
        ),
        **(
            {"commentEnds": block.comment_ends}
            if block.comment_ends is not None
            else {}
        ),
        **({"sdtShell": block.sdt_shell} if block.sdt_shell is not None else {}),
    }


def _apply_set(item: dict, op: dict) -> None:
    block = item.get("sourceBlock")
    if block is None or item.get("final", {}).get("kind") != "original":
        raise ValueError(
            "a block may be set only once per docx call; combine desired changes in one set op"
        )
    has_text = "text" in op
    has_runs = "runs" in op
    has_format = "format" in op
    if has_text and has_runs:
        raise ValueError("set accepts either text or runs, not both")
    if has_text and not has_format:
        if not block.original_xml or block.type not in (
            "paragraph",
            "heading",
            "listItem",
        ):
            raise ValueError(
                "text-only set requires a text paragraph; use replace for this block"
            )
        runs = block.runs or []
        if any(r.math or r.note_ref for r in runs):
            raise ValueError(
                "text-only set is ambiguous for paragraphs with math/notes; use runs or replace"
            )
        # A full-text replacement has no principled way to redistribute new text
        # over differently formatted runs. Reject instead of silently moving all
        # text into the first style/link/comment range. Uniformly styled split
        # runs remain safe for the surgical w:t patch.
        signatures = []
        for run in runs:
            model = _run_json(run, include_raw=True)
            model.pop("text", None)
            signatures.append(
                json.dumps(model, ensure_ascii=False, sort_keys=True, default=str)
            )
        if len(set(signatures)) > 1:
            raise ValueError(
                "text-only set is ambiguous for mixed-format runs; use runs or replace"
            )
        patched = patch_paragraph_texts(block.original_xml, str(op.get("text", "")))
        if patched is None:
            raise ValueError(
                "cannot preserve this paragraph with a surgical text patch; use replace with runs"
            )
        item["final"] = {"kind": "xml", "xml": patched}
        return
    model = _generated_from_source(block)
    if has_runs:
        model["runs"] = [_normalize_run(r) for r in (op.get("runs") or [])]
    elif has_text:
        # Explicit text + format: preserve the first run's style and keep the
        # requested paragraph formatting; callers needing mixed styles use runs.
        old_runs = model.get("runs") or [{"text": ""}]
        first = dict(old_runs[0])
        first["text"] = str(op.get("text", ""))
        model["runs"] = [first]
    if has_format:
        incoming = op.get("format") or {}
        if not isinstance(incoming, dict):
            raise ValueError("set.format must be an object")
        merged = dict(model.get("format") or {})
        merged.update(incoming)
        model["format"] = merged
    for key in ("styleId", "level", "list"):
        if key in op:
            model[key] = op[key]
    item["final"] = {"kind": "generated", "block": model}


def _apply_edit_ops(parsed, ops: list[dict], options: dict) -> list[dict]:
    items = _original_items(parsed)
    for op in ops:
        if not isinstance(op, dict):
            raise ValueError("each edit op must be an object")
        kind = str(op.get("op", "")).lower()
        if kind == "insert":
            where = str(op.get("where") or "after").lower()
            new = [
                {"sourceId": None, "sourceBlock": None, "final": fb}
                for fb in _blocks_to_final(op.get("blocks"), parsed, options)
            ]
            if where in ("start", "begin"):
                pos = 0
            elif where in ("end", "append"):
                pos = len(items)
            elif where in ("before", "after"):
                idx = _find_item_indexes(items, op.get("target"))[0]
                pos = idx if where == "before" else idx + 1
            else:
                raise ValueError("insert.where must be start, end, before, or after")
            items[pos:pos] = new
        elif kind == "replace":
            idxs = _find_item_indexes(items, op.get("target"))
            if len(idxs) != 1:
                raise ValueError("replace targets exactly one block")
            idx = idxs[0]
            new = [
                {"sourceId": None, "sourceBlock": None, "final": fb}
                for fb in _blocks_to_final(op.get("blocks"), parsed, options)
            ]
            items[idx : idx + 1] = new
        elif kind == "delete":
            for idx in sorted(
                _find_item_indexes(items, op.get("target")), reverse=True
            ):
                del items[idx]
        elif kind == "set":
            idxs = _find_item_indexes(items, op.get("target"))
            if len(idxs) != 1:
                raise ValueError("set targets exactly one block")
            _apply_set(items[idxs[0]], op)
        else:
            raise ValueError("edit op must be insert, replace, delete, or set")
    return [item["final"] for item in items]


def _merge_options(base: dict | None) -> dict:
    return copy.deepcopy(base) if isinstance(base, dict) else {}


def _write_result(path: Path, data: bytes, command: str, *, changed: str) -> ToolResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    from tools.file_ops.read import invalidate_read_cache

    invalidate_read_cache(path)
    # Re-parse the exact bytes written. This is both validation and a compact
    # fresh-id summary for the next agent action.
    parsed = parse_docx(data)
    visible = [b for b in parsed.blocks if not b.hidden]
    return ToolResult(
        name="docx",
        status="ok",
        exit_code=0,
        command=command,
        output=f"DOCX {changed}: {path} · {len(data)} bytes · {len(visible)} blocks. Re-read only if you need current block ids/content.",
    )


def _help_text(topic: str) -> str:
    topic = (topic or "blocks").lower()
    docs = {
        "blocks": (
            "DOCX blocks: p {text|runs,format?}; h1..h9 or heading {level,text|runs}; "
            "li {list:'bullet'|'ordered',level?,text|runs}; table {rows:[[cell,...]],header?}; "
            "math {latex,align?}; image {path,widthPx?,heightPx?,align?,wrap?}; "
            "chart {kind,title?,categories,series}; pageBreak; toc {entries}; caption {label,number,text}. "
            "Plain strings are paragraph shorthand. Raw OOXML is intentionally not accepted."
        ),
        "runs": (
            "Run fields: text,bold,italic,underline,strike,color,font,size(points),highlight,vertAlign,link,latex; "
            "advanced engine fields also pass through: styleId,charSpacingTwips,charScalePct,em,noteRef,commentIds,ins,del,rPrChange,ruby. "
            "Paragraph format uses camelCase: align,lineSpacing,lineRule,indentLeft,indentRight,indentFirstLine,spaceBefore,spaceAfter,"
            "pageBreakBefore,keepNext,keepLines,widowControl,contextualSpacing,shadingFill,borders,tabStops,dropCap,bidi."
        ),
        "edit": (
            "Edit uses ids from the current read version. Batch independent ops: "
            "insert {where:'start'|'end'|'before'|'after',target?,blocks}; replace {target,blocks}; "
            "delete {target:id|[ids]}; set {target,text? OR runs?,format?,styleId?,level?,list?}. "
            "set(text) is for uniform text runs; use runs/replace for mixed formatting, links, math, or notes. "
            "Structural edits can shift bN ids; re-read only before a later call that needs fresh ids."
        ),
        "options": (
            "Document options are the native engine options object. Common: "
            "header:{text|paras}, footer:{text|paras,pageNumber?}, watermark:'TEXT', section:{orientation,pageWidth,pageHeight,marginTop,marginRight,marginBottom,marginLeft,...}, "
            "comments:[{id,author,text,initials?,date?}], footnotes/endnotes:[{id,text}], sources:[...], themeFonts/themeColors, protection, numbering, savedAt. "
            "Use inspect without target for current document metadata. Prefer semantic fields; includeRaw/includeMedia only for diagnosis."
        ),
    }
    if topic not in docs:
        raise ValueError("help topic must be blocks, runs, edit, or options")
    return docs[topic]


def docx(call: ToolCall) -> ToolResult:
    """Create, edit, inspect, or explain native DOCX operations."""
    args = call.args or {}
    action = str(args.get("action") or "").lower()
    if action == "help":
        try:
            return ToolResult(
                name="docx",
                status="ok",
                output=_help_text(str(args.get("topic") or "blocks")),
                exit_code=0,
                command=call.command,
            )
        except Exception as exc:
            return ToolResult(
                name="docx",
                status="error",
                output=f"DOCX error: {exc}",
                exit_code=1,
                command=call.command,
            )
    path_str = clean_path(args.get("path", ""))
    if not path_str:
        return ToolResult(
            name="docx",
            status="error",
            output="path is required for create/edit/inspect",
            exit_code=1,
            command=call.command,
        )
    path = resolve_path(path_str, extensions=(".docx",))
    if path.suffix.lower() != ".docx":
        path = path.with_suffix(".docx")

    try:
        if action == "create":
            if path.exists() and args.get("overwrite") is False:
                raise ValueError(f"file already exists: {path}")
            parsed = parse_docx(build_blank_docx(args.get("eastAsiaFont")))
            options = _merge_options(args.get("options"))
            blocks = _blocks_to_final(args.get("blocks") or [], parsed, options)
            # A genuinely empty create still needs the blank paragraph so Word
            # has a caret location.
            if not blocks:
                blocks = [{"kind": "original", "docxIndex": 0}]
            data = save_docx(parsed, blocks, options)
            return _write_result(path, data, call.command, changed="created")

        if not path.is_file():
            raise ValueError(f"DOCX file not found: {path_str}")
        parsed = parse_docx(path.read_bytes())

        if action == "inspect":
            target = args.get("target")
            include_raw = bool(args.get("includeRaw", False))
            include_media = bool(args.get("includeMedia", False))
            visible = [b for b in parsed.blocks if not b.hidden]
            payload: dict[str, Any] = {}
            if target is not None:
                wanted = target if isinstance(target, list) else [target]
                selected = []
                for raw in wanted:
                    tok = str(raw)
                    b = next(
                        (
                            x
                            for x in visible
                            if x.id == tok
                            or (tok.isdigit() and x.docx_index == int(tok))
                        ),
                        None,
                    )
                    if b is None:
                        raise ValueError(f"DOCX target not found: {raw!r}")
                    selected.append(b)
                payload["blocks"] = [
                    _block_json(b, include_raw=include_raw, include_media=include_media)
                    for b in selected
                ]
            if bool(args.get("includeMeta", target is None)):
                payload["meta"] = _doc_meta_json(parsed)
            if not payload:
                payload["blocks"] = []
            return ToolResult(
                name="docx",
                status="ok",
                output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                exit_code=0,
                command=call.command,
            )

        if action == "edit":
            ops = args.get("ops") or []
            if not isinstance(ops, list) or not ops:
                raise ValueError("edit requires a non-empty ops array")
            options = _merge_options(args.get("options"))
            final_blocks = _apply_edit_ops(parsed, ops, options)
            data = save_docx(parsed, final_blocks, options)
            out_arg = clean_path(args.get("out", ""))
            out_path = resolve_path(out_arg, extensions=(".docx",)) if out_arg else path
            if out_path.suffix.lower() != ".docx":
                out_path = out_path.with_suffix(".docx")
            if (
                out_path != path
                and out_path.exists()
                and args.get("overwrite") is False
            ):
                raise ValueError(f"file already exists: {out_path}")
            return _write_result(out_path, data, call.command, changed="updated")

        raise ValueError("action must be create, edit, inspect, or help")
    except Exception as exc:
        logger.opt(exception=True).warning("docx tool failed: {}", exc)
        return ToolResult(
            name="docx",
            status="error",
            output=f"DOCX error: {exc}",
            exit_code=1,
            command=call.command,
        )


__all__ = ["docx", "read_docx_compact"]
