"""word/footnotes.xml и word/endnotes.xml."""

from __future__ import annotations

import re

from .models import NoteInfo
from .text_patch import patch_paragraph_texts
from .xml_utils import escape_xml_attr, escape_xml_text, unescape_xml_text

ROOT = {"footnote": "w:footnotes", "endnote": "w:endnotes"}
ENTRY = {"footnote": "w:footnote", "endnote": "w:endnote"}
NOTE_PART_PATH = {"footnote": "word/footnotes.xml", "endnote": "word/endnotes.xml"}
NOTE_REL_TYPE = {
    "footnote": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
    "endnote": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes",
}
NOTE_CONTENT_TYPE = {
    "footnote": "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
    "endnote": "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
}
NOTE_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def _plain(xml):
    return unescape_xml_text(
        "".join(re.findall(r"<w:t(?:\s[^>]*)?>([\s\S]*?)</w:t>", xml))
    )


def _entries(xml: str, kind: str):
    out = []
    entry = ENTRY[kind]
    for m in re.finditer(rf"<{entry}(\s[^>]*)?>([\s\S]*?)</{entry}>", xml):
        attrs = m.group(1) or ""
        if 'w:type="' in attrs:
            continue
        idm = re.search(r'w:id="([^"]+)"', attrs)
        if not idm:
            continue
        paras = [
            re.sub(r"^\s+", "", _plain(p), count=1) if i == 0 else _plain(p)
            for i, p in enumerate(
                re.findall(r"<w:p[\s>][\s\S]*?</w:p>|<w:p/>", m.group(2))
            )
        ]
        out.append({"id": idm.group(1), "text": "\n".join(paras), "xml": m.group(0)})
    return out


def parse_notes_xml(xml: str, kind: str) -> list[NoteInfo]:
    return [NoteInfo(id=e["id"], text=e["text"]) for e in _entries(xml, kind)]


def _separators(kind: str) -> str:
    e = ENTRY[kind]
    return (
        f'<{e} w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0" w:line="240" '
        f'w:lineRule="auto"/></w:pPr><w:r><w:separator/></w:r></w:p></{e}>'
        f'<{e} w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing w:after="0" '
        f'w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:continuationSeparator/></w:r></w:p></{e}>'
    )


def _note_xml(kind: str, n: NoteInfo) -> str:
    e = ENTRY[kind]
    ref = "w:footnoteRef" if kind == "footnote" else "w:endnoteRef"
    paras = []
    for i, line in enumerate(n.text.split("\n")):
        ref_run = (
            (
                f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><{ref}/></w:r>'
                '<w:r><w:t xml:space="preserve"> </w:t></w:r>'
            )
            if i == 0
            else ""
        )
        text_run = (
            ""
            if line == ""
            else f'<w:r><w:t xml:space="preserve">{escape_xml_text(line)}</w:t></w:r>'
        )
        paras.append(f"<w:p>{ref_run}{text_run}</w:p>")
    return f'<{e} w:id="{escape_xml_attr(n.id)}">{"".join(paras)}</{e}>'


def build_notes_xml(kind: str, notes: list[NoteInfo], original_xml=None) -> str:
    entry = ENTRY[kind]
    structural = ""
    originals = {}
    if original_xml:
        structural = "".join(
            re.findall(
                rf'<{entry}\s[^>]*w:type="[^"]*"[^>]*>[\s\S]*?</{entry}>', original_xml
            )
        )
        for e in _entries(original_xml, kind):
            originals[e["id"]] = e
    if not structural:
        structural = _separators(kind)
    body = []
    for n in notes:
        orig = originals.get(n.id)
        if not orig:
            body.append(_note_xml(kind, n))
        elif orig["text"] == n.text:
            body.append(orig["xml"])
        else:
            patched = patch_paragraph_texts(
                orig["xml"], n.text, strip_first_leading_space=True
            )
            body.append(patched if patched else _note_xml(kind, n))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f"<{ROOT[kind]} {NOTE_NS}>{structural}{''.join(body)}</{ROOT[kind]}>"
    )


def next_note_id(notes: list[NoteInfo]) -> str:
    """Return the next positive user-note id.

    Negative/zero ids are structural/reserved in Word footnote/endnote parts and
    intentionally do not advance the user-id sequence. Existing positive ids
    are still considered, so the returned id cannot collide with them.
    """
    mx = 0
    for n in notes:
        try:
            value = int(n.id)
        except (TypeError, ValueError):
            continue
        if value > 0:
            mx = max(mx, value)
    return str(mx + 1)
