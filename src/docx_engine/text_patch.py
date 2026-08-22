"""Хирургический патч w:t внутри абзацев."""

from __future__ import annotations

import re

from .xml_utils import escape_xml_text, unescape_xml_text

_P_RE = re.compile(r"<w:p(?:\s[^>]*)?\/>|<w:p(?:\s[^>]*)?>|</w:p>")
_T_RE = re.compile(r"<w:t(\s[^>]*)?>([\s\S]*?)</w:t>")


def _para_slices(xml: str):
    out, depth, start = [], 0, -1
    for m in _P_RE.finditer(xml):
        tok = m.group(0)
        if tok == "</w:p>":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start != -1:
                out.append(
                    {"start": start, "end": m.end(), "xml": xml[start : m.end()]}
                )
                start = -1
        elif tok.endswith("/>"):
            if depth == 0:
                out.append({"start": m.start(), "end": m.end(), "xml": tok})
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def _t_slices(para_xml: str):
    out = []
    for m in _T_RE.finditer(para_xml):
        inner = m.group(2)
        inner_start = m.end() - len(inner) - len("</w:t>")
        out.append(
            {
                "tagStart": m.start(),
                "innerStart": inner_start,
                "innerEnd": inner_start + len(inner),
                "text": unescape_xml_text(inner),
            }
        )
    return out


def _decoded(para_xml):
    return "".join(t["text"] for t in _t_slices(para_xml))


def _patch_one(para_xml: str, new_text: str, skip_leading: bool):
    slices = _t_slices(para_xml)
    if not slices:
        return None
    raw = "".join(t["text"] for t in slices)
    lead = (len(raw) - len(raw.lstrip())) if skip_leading else 0
    old = raw[lead:]
    if old == new_text:
        return para_xml
    prefix = 0
    mx = min(len(old), len(new_text))
    while prefix < mx and old[prefix] == new_text[prefix]:
        prefix += 1
    suffix = 0
    while suffix < mx - prefix and old[-1 - suffix] == new_text[-1 - suffix]:
        suffix += 1
    change_start, change_end = lead + prefix, lead + len(old) - suffix
    replacement = new_text[prefix : len(new_text) - suffix if suffix else len(new_text)]
    acc, first, last, bounds = 0, -1, -1, []
    for i, s in enumerate(slices):
        st, en = acc, acc + len(s["text"])
        bounds.append((st, en))
        touches = (
            (st <= change_start <= en)
            if change_start == change_end
            else (st < change_end and en > change_start)
        )
        if touches:
            if first == -1:
                first = i
            last = i
        acc = en
    if first == -1:
        return None
    new_inner = []
    for i in range(first, last + 1):
        if i == first:
            head = slices[i]["text"][: change_start - bounds[i][0]]
            tail = slices[last]["text"][change_end - bounds[last][0] :]
            new_inner.append(escape_xml_text(head + replacement + tail))
        else:
            new_inner.append("")
    out = para_xml
    for i in range(last, first - 1, -1):
        s = slices[i]
        close_end = s["innerEnd"] + len("</w:t>")
        out = (
            out[: s["tagStart"]]
            + f'<w:t xml:space="preserve">{new_inner[i - first]}</w:t>'
            + out[close_end:]
        )
    return out


def patch_paragraph_texts(
    entry_xml: str, new_text: str, strip_first_leading_space=False
):
    paras = _para_slices(entry_xml)
    if not paras:
        return None
    old_texts = []
    for i, p in enumerate(paras):
        t = _decoded(p["xml"])
        if i == 0 and strip_first_leading_space:
            t = t.lstrip()
        old_texts.append(t)
    if "\n".join(old_texts) == new_text:
        return entry_xml
    new_paras = new_text.split("\n")
    if len(new_paras) != len(paras):
        return None
    out, cursor = "", 0
    for i, p in enumerate(paras):
        out += entry_xml[cursor : p["start"]]
        if old_texts[i] == new_paras[i]:
            out += p["xml"]
        else:
            patched = _patch_one(
                p["xml"], new_paras[i], i == 0 and strip_first_leading_space
            )
            if patched is None:
                return None
            out += patched
        cursor = p["end"]
    out += entry_xml[cursor:]
    check = []
    for i, p in enumerate(_para_slices(out)):
        t = _decoded(p["xml"])
        if i == 0 and strip_first_leading_space:
            t = t.lstrip()
        check.append(t)
    return out if "\n".join(check) == new_text else None
