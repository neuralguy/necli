"""Byte-exact сканер <w:body>."""

from __future__ import annotations

import re

TAG_RE = re.compile(
    r"<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|</?(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>"
)
NAME_RE = re.compile(r"^<\/?\s*([A-Za-z_][\w:.-]*)")


def scan_body(document_xml: str) -> dict:
    m = re.search(r"<w:body(?:\s(?:[^<>\"']|\"[^\"]*\"|'[^']*')*)?>", document_xml)
    if not m:
        raise ValueError("document.xml has no <w:body> element")
    scan_from = m.end()
    elements: list[dict] = []
    depth = 0
    cur_start = -1
    cur_name = ""
    for t in TAG_RE.finditer(document_xml, scan_from):
        tag = t.group(0)
        if (
            tag.startswith("<!--")
            or tag.startswith("<![")
            or tag.startswith("<?")
            or tag.startswith("<!")
        ):
            continue
        is_closing = tag.startswith("</")
        is_self = (not is_closing) and tag.endswith("/>")
        nm = NAME_RE.match(tag)
        name = nm.group(1) if nm else ""
        if is_closing:
            if depth == 0:
                if name == "w:body":
                    break
                raise ValueError(f"unexpected closing tag </{name}> at body level")
            depth -= 1
            if depth == 0:
                elements.append({"name": cur_name, "start": cur_start, "end": t.end()})
        elif is_self:
            if depth == 0:
                elements.append({"name": name, "start": t.start(), "end": t.end()})
        else:
            if depth == 0:
                cur_start, cur_name = t.start(), name
            depth += 1
    if not elements:
        return {"elements": [], "innerStart": scan_from, "innerEnd": scan_from}
    return {
        "elements": elements,
        "innerStart": elements[0]["start"],
        "innerEnd": elements[-1]["end"],
    }
