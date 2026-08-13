"""Ink-аннотации: плавающие картинки necli-ink."""

from __future__ import annotations

import math
import re

from .xml_utils import escape_xml_attr, unescape_xml_text

INK_NAME_PREFIX = "necli-ink"
INK_MEDIA_PREFIX = "necliink"
INK_REL_RE = re.compile(
    rf'<Relationship [^>]*Target="media/{INK_MEDIA_PREFIX}\d+\.png"[^>]*/>', re.S
)
INK_MEDIA_PATH_RE = re.compile(rf"^word/media/{INK_MEDIA_PREFIX}\d+\.png$")
EMU_PER_PX = 9525
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ANCHOR_RUN_RE = re.compile(r"<w:r><w:drawing><wp:anchor[\s\S]*?</wp:anchor></w:drawing></w:r>")


def anchored_ink_run_xml(ink: dict, r_id: str, doc_pr_id: int) -> str:
    try:
        width = float(ink["widthPx"])
        height = float(ink["heightPx"])
        offset_x = float(ink.get("offsetXPx", 0))
        offset_y = float(ink.get("offsetYPx", 0))
        doc_pr_id = int(doc_pr_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ink dimensions/offsets/id must be numeric") from exc
    if (
        not all(math.isfinite(v) for v in (width, height, offset_x, offset_y))
        or width <= 0
        or height <= 0
        or doc_pr_id < 0
    ):
        raise ValueError("ink dimensions must be finite positive values and id non-negative")
    cx = max(1, round(width * EMU_PER_PX))
    cy = max(1, round(height * EMU_PER_PX))
    x, y = round(offset_x * EMU_PER_PX), round(offset_y * EMU_PER_PX)
    r_id = str(r_id)
    name = f"{INK_NAME_PREFIX} {doc_pr_id}"
    descr = f' descr="{escape_xml_attr(ink["payload"])}"' if ink.get("payload") else ""
    return (
        "<w:r><w:drawing>"
        f'<wp:anchor xmlns:wp="{WP_NS}" distT="0" distB="0" distL="0" distR="0" simplePos="0"'
        f' relativeHeight="{251658240 + doc_pr_id}" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="column"><wp:posOffset>{x}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="paragraph"><wp:posOffset>{y}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        "<wp:wrapNone/>"
        f'<wp:docPr id="{doc_pr_id}" name="{name}"{descr}/>'
        "<wp:cNvGraphicFramePr/>"
        f'<a:graphic xmlns:a="{A_NS}">'
        f'<a:graphicData uri="{PIC_NS}">'
        f'<pic:pic xmlns:pic="{PIC_NS}">'
        f'<pic:nvPicPr><pic:cNvPr id="{doc_pr_id}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip xmlns:r="{R_NS}" r:embed="{escape_xml_attr(r_id)}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>"
    )


def _is_ink(run):
    return f'name="{INK_NAME_PREFIX}' in run


def strip_ink_runs(xml: str) -> str:
    if INK_NAME_PREFIX not in xml:
        return xml
    return ANCHOR_RUN_RE.sub(lambda m: "" if _is_ink(m.group(0)) else m.group(0), xml)


def find_ink_runs(paragraph_xml: str) -> list[dict]:
    if INK_NAME_PREFIX not in paragraph_xml:
        return []
    out = []
    for run in ANCHOR_RUN_RE.findall(paragraph_xml):
        if not _is_ink(run):
            continue

        def emu(pat, _run=run):
            m = re.search(pat, _run)
            try:
                return int(m.group(1)) if m else 0
            except (ValueError, AttributeError):
                return 0

        descr = re.search(r'<wp:docPr [^>]*descr="([^"]*)"', run)
        out.append(
            {
                "xml": run,
                "offsetXPx": emu(r"<wp:positionH[^>]*><wp:posOffset>(-?\d+)") / EMU_PER_PX,
                "offsetYPx": emu(r"<wp:positionV[^>]*><wp:posOffset>(-?\d+)") / EMU_PER_PX,
                "widthPx": emu(r'<wp:extent cx="(\d+)"') / EMU_PER_PX,
                "heightPx": emu(r'<wp:extent cx="\d+" cy="(\d+)"') / EMU_PER_PX,
                "payload": unescape_xml_text(descr.group(1)) if descr else None,
                "embedRId": (re.search(r'r:embed="([^"]+)"', run) or [None, None])[1],
            }
        )
    return out


def inject_ink_runs_into_paragraph(xml: str, runs_xml: str):
    if not re.match(r"<w:p[\s/>]", xml):
        return None
    if xml.endswith("/>"):
        return xml[:-2] + ">" + runs_xml + "</w:p>"
    close = xml.rfind("</w:p>")
    if close == -1:
        return None
    return xml[:close] + runs_xml + xml[close:]
