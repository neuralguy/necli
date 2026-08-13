"""Библиография b:Sources в customXml."""

from __future__ import annotations

import re

from .models import SourceInfo
from .xml_utils import escape_xml_text, unescape_xml_text

SOURCES_NS = "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"
CUSTOM_XML_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"
)


def _dec(s: str) -> str:
    return unescape_xml_text(s)


def parse_sources_xml(xml: str) -> list[SourceInfo]:
    out = []
    for m in re.finditer(r"<b:Source>([\s\S]*?)</b:Source>", xml):
        body = m.group(1)

        def fld(tag, _body=body):
            f = re.search(rf"<b:{tag}>([\s\S]*?)</b:{tag}>", _body)
            return _dec(f.group(1).strip()) if f else None

        tag = fld("Tag")
        if not tag:
            continue
        author = fld("Corporate") or ""
        if not author:
            last, first = fld("Last") or "", fld("First") or ""
            author = ", ".join(x for x in (last, first) if x)
        out.append(
            SourceInfo(
                tag=tag,
                type=fld("SourceType") or "Misc",
                author=author,
                title=fld("Title") or "",
                year=fld("Year") or "",
                publisher=fld("Publisher") or fld("JournalName") or fld("InternetSiteTitle"),
                url=fld("URL"),
            )
        )
    return out


def _source_xml(s: SourceInfo) -> str:
    def t(tag, v):
        return f"<b:{tag}>{escape_xml_text(str(v))}</b:{tag}>" if v is not None and v != "" else ""

    comma = s.author.find(",")
    last = s.author if comma == -1 else s.author[:comma].strip()
    first = "" if comma == -1 else s.author[comma + 1 :].strip()
    author_xml = (
        (
            "<b:Author><b:Author><b:NameList><b:Person>"
            + t("Last", last)
            + t("First", first)
            + "</b:Person></b:NameList></b:Author></b:Author>"
        )
        if s.author
        else ""
    )
    pub = {"JournalArticle": "JournalName", "InternetSite": "InternetSiteTitle"}.get(
        s.type, "Publisher"
    )
    return (
        "<b:Source>"
        + t("Tag", s.tag)
        + t("SourceType", s.type)
        + author_xml
        + t("Title", s.title)
        + t("Year", s.year)
        + t(pub, s.publisher)
        + t("URL", s.url)
        + "</b:Source>"
    )


def build_sources_xml(sources: list[SourceInfo], original_xml=None) -> str:
    originals = {}
    root_open = (
        f'<b:Sources SelectedStyle="\\APASixthEditionOfficeOnline.xsl" StyleName="APA" '
        f'Version="6" xmlns:b="{SOURCES_NS}" xmlns="{SOURCES_NS}">'
    )
    if original_xml:
        m = re.search(r"<b:Sources(?:\s[^>]*)?>", original_xml)
        if m:
            root_open = m.group(0)
        for mm in re.finditer(r"<b:Source>[\s\S]*?</b:Source>", original_xml):
            parsed = parse_sources_xml(mm.group(0))
            if parsed:
                originals[parsed[0].tag] = (mm.group(0), parsed[0])

    def unchanged(a, b):
        return (
            a.type == b.type
            and a.author == b.author
            and a.title == b.title
            and a.year == b.year
            and (a.publisher or "") == (b.publisher or "")
            and (a.url or "") == (b.url or "")
        )

    body = []
    for s in sources:
        orig = originals.get(s.tag)
        body.append(orig[0] if orig and unchanged(orig[1], s) else _source_xml(s))
    return root_open + "".join(body) + "</b:Sources>"


def build_sources_item_props_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<ds:datastoreItem ds:itemID="{4A8D2934-D5B1-4C15-A6F4-7A1B2C3D4E5F}"'
        ' xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml">'
        f'<ds:schemaRefs><ds:schemaRef ds:uri="{SOURCES_NS}"/></ds:schemaRefs>'
        "</ds:datastoreItem>"
    )


def citation_text(s: SourceInfo) -> str:
    author = s.author or s.title
    return f"({author}, {s.year})" if s.year else f"({author})"


def bibliography_line(s: SourceInfo) -> str:
    parts = []
    if s.author:
        parts.append(s.author + ".")
    if s.year:
        parts.append(f"({s.year}).")
    if s.title:
        parts.append(s.title + ".")
    if s.publisher:
        parts.append(s.publisher + ".")
    if s.url:
        parts.append(s.url)
    return " ".join(parts)


def find_sources_part(zip_file):
    """Return the bibliography customXml part, if present."""
    for name in zip_file.namelist():
        if not re.fullmatch(r"customXml/item\d+\.xml", name):
            continue
        xml = zip_file.read(name).decode("utf-8")
        if "Sources" in xml and SOURCES_NS in xml:
            return name
    return None


# Backwards-compatible synchronous alias used by the parser.
find_sources_part_sync = find_sources_part
