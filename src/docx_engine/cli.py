"""Агентный CLI-мост: line-delimited JSON на stdin/stdout.

Запуск:  python -m docx_engine.cli

Протокол (одна JSON-строка = одна команда, ответ — одна JSON-строка):
  {"cmd":"new","out":"blank.docx","eastAsiaFont":"SimSun"}
  {"cmd":"parse","path":"in.docx"}                       → модель документа
  {"cmd":"blockXml","path":"in.docx","index":3}          → сырой XML блока
  {"cmd":"save","path":"in.docx","out":"out.docx",
     "blocks":[{"kind":"original","docxIndex":0},
               {"kind":"generated","block":{"type":"heading","level":1,
                 "runs":[{"text":"Заголовок","bold":true}]}}],
     "options":{"watermark":"CONFIDENTIAL"}}
  {"cmd":"latex2omml","latex":"\\frac{a}{b}"}
  {"cmd":"toc","entries":[{"level":1,"text":"Введение"}]}
  {"cmd":"table","rows":3,"cols":4,"headerRow":true}
  {"cmd":"hashPassword","password":"secret"}
  {"cmd":"quit"}
"""

from __future__ import annotations

import json
import sys
import traceback

from .blank import build_blank_docx
from .chart import patch_chart_part_xml
from .generate import (
    build_textbox_paragraph_xml,
    build_word_art_paragraph_xml,
    generate_caption_xml,
    generate_table_xml,
    generate_toc_field_xml,
)
from .mathml import latex_to_omml, omml_to_mathml
from .models import Run
from .parse import parse_docx
from .patch import save_docx
from .protection import hash_protection_password


def _revision_to_json(v):
    if v is None:
        return None
    return {
        "author": v.author,
        **({"date": v.date} if v.date is not None else {}),
        **({"id": v.id} if v.id is not None else {}),
    }


def _format_to_json(f):
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
            # False/0 are semantically meaningful and must survive the bridge.
            out[key] = value
    return out


def _run_to_json(r):
    d = {"text": r.text}
    fields = (
        ("raw_r_pr", "rawRPr"),
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
    if r.ins is not None:
        d["ins"] = _revision_to_json(r.ins)
    if r.del_ is not None:
        d["del"] = _revision_to_json(r.del_)
    return d


def _block_to_json(b):
    d = {
        "id": b.id,
        "type": b.type,
        "docxIndex": b.docx_index,
        "hidden": b.hidden,
        "decorative": b.decorative,
    }
    fields = (
        ("original_xml", "originalXml"),
        ("level", "level"),
        ("style_id", "styleId"),
        ("list", "list"),
        ("raw_p_pr", "rawPPr"),
        ("label", "label"),
        ("preview_text", "previewText"),
        ("image_data_url", "imageDataUrl"),
        ("image_width_px", "imageWidthPx"),
        ("image_height_px", "imageHeightPx"),
        ("image_align", "imageAlign"),
        ("image_wrap", "imageWrap"),
        ("table", "table"),
        ("field_display", "fieldDisplay"),
        ("sdt_shell", "sdtShell"),
        ("bookmarks", "bookmarks"),
        ("hidden_bookmarks", "hiddenBookmarks"),
        ("comment_starts", "commentStarts"),
        ("comment_ends", "commentEnds"),
        ("move_revision", "moveRevision"),
        ("p_pr_change_info", "pPrChangeInfo"),
        ("block_revision", "blockRevision"),
        ("chart_display", "chartDisplay"),
        ("formula_display", "formulaDisplay"),
    )
    for attr, key in fields:
        value = getattr(b, attr)
        if value is not None:
            d[key] = value
    if b.format is not None:
        d["format"] = _format_to_json(b.format)
    if b.runs is not None:
        d["text"] = "".join(r.text for r in b.runs)
        d["runs"] = [_run_to_json(r) for r in b.runs]
    return d


def _run_to_obj(r):
    if isinstance(r, Run):
        return r
    # save_docx has the canonical coercion logic; keeping the JSON structure
    # intact here prevents the CLI from silently dropping advanced run fields.
    return r


def handle(cmd: dict):
    c = cmd.get("cmd")
    if c == "new":
        data = build_blank_docx(cmd.get("eastAsiaFont"))
        if cmd.get("out"):
            open(cmd["out"], "wb").write(data)
        return {"ok": True, "bytes": len(data), "out": cmd.get("out")}
    if c == "parse":
        parsed = parse_docx(open(cmd["path"], "rb").read())
        return {
            "ok": True,
            "blocks": [_block_to_json(b) for b in parsed.blocks],
            "comments": [
                {
                    "id": x.id,
                    "author": x.author,
                    "text": x.text,
                    "done": x.done,
                    **({"initials": x.initials} if x.initials is not None else {}),
                    **({"date": x.date} if x.date is not None else {}),
                    **({"parentId": x.parent_id} if x.parent_id is not None else {}),
                    **({"paraId": x.para_id} if x.para_id is not None else {}),
                }
                for x in parsed.comments
            ],
            "footnotes": [
                {
                    "id": x.id,
                    "text": x.text,
                    **({"richParas": x.rich_paras} if x.rich_paras is not None else {}),
                }
                for x in parsed.footnotes
            ],
            "endnotes": [
                {
                    "id": x.id,
                    "text": x.text,
                    **({"richParas": x.rich_paras} if x.rich_paras is not None else {}),
                }
                for x in parsed.endnotes
            ],
            "sources": [
                {
                    "tag": x.tag,
                    "type": x.type,
                    "author": x.author,
                    "title": x.title,
                    "year": x.year,
                    **({"publisher": x.publisher} if x.publisher is not None else {}),
                    **({"url": x.url} if x.url is not None else {}),
                }
                for x in parsed.sources
            ],
            "inks": parsed.inks,
            "protection": parsed.protection,
            "headerText": parsed.header_text,
            "footerText": parsed.footer_text,
            "footerHasPageNumber": parsed.footer_has_page_number,
            "watermarkText": parsed.watermark_text,
            "titlePg": parsed.title_pg,
            "evenAndOddHeaders": parsed.even_and_odd_headers,
            "headingStyleIds": parsed.heading_style_ids,
            "listParagraphStyleId": parsed.list_paragraph_style_id,
            "themeFonts": parsed.theme_fonts,
            "themeColors": parsed.theme_colors,
            "styles": [dict(s) for s in parsed.styles.values() if not s.get("semiHidden")],
            "docDefaults": parsed.doc_defaults,
            "numbering": parsed.numbering,
        }
    if c == "blockXml":
        parsed = parse_docx(open(cmd["path"], "rb").read())
        b = next(x for x in parsed.blocks if x.docx_index == cmd["index"])
        return {"ok": True, "xml": b.original_xml}
    if c == "save":
        parsed = parse_docx(open(cmd["path"], "rb").read())
        blocks = []
        for fb in cmd.get("blocks") or []:
            blocks.append(fb)
        out = save_docx(parsed, blocks, cmd.get("options") or {})
        if cmd.get("out"):
            open(cmd["out"], "wb").write(out)
        return {"ok": True, "bytes": len(out), "out": cmd.get("out")}
    if c == "latex2omml":
        omml = latex_to_omml(cmd["latex"])
        return {"ok": True, "omml": omml, "mathml": omml_to_mathml(omml)}
    if c == "toc":
        return {"ok": True, "xml": generate_toc_field_xml(cmd.get("entries", []))}
    if c == "caption":
        return {
            "ok": True,
            "xml": generate_caption_xml(cmd["label"], cmd["number"], cmd.get("text", "")),
        }
    if c == "table":
        return {
            "ok": True,
            "xml": generate_table_xml(
                cmd.get("rows", 2), cmd.get("cols", 2), cmd.get("headerRow", False)
            ),
        }
    if c == "textbox":
        return {"ok": True, "xml": build_textbox_paragraph_xml()}
    if c == "wordart":
        return {
            "ok": True,
            "xml": build_word_art_paragraph_xml(cmd.get("text", "WordArt"), cmd.get("wordArtId")),
        }
    if c == "patchChart":
        return {"ok": True, "xml": patch_chart_part_xml(cmd["xml"], cmd["patch"])}
    if c == "hashPassword":
        return {
            "ok": True,
            **hash_protection_password(cmd["password"], cmd.get("spinCount", 100000)),
        }
    if c in ("quit", "exit"):
        return None
    return {"ok": False, "error": f"unknown command: {c}"}


def main():
    interactive = sys.stdin.isatty()
    if interactive:
        print('docx_engine CLI — JSON-команды (help: {"cmd":"..."})', file=sys.stderr)
    while True:
        try:
            if interactive:
                sys.stderr.write("> ")
                sys.stderr.flush()
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            cmd = json.loads(line)
            result = handle(cmd)
            if result is None:
                break
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"invalid JSON: {e}"}), flush=True)
        except Exception:
            print(
                json.dumps(
                    {"ok": False, "error": traceback.format_exc(limit=5)}, ensure_ascii=False
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
