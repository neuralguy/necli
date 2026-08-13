"""Пустой шаблон .docx."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
DOC_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
BLANK_BULLET_NUM_ID = "1"
BLANK_ORDERED_NUM_ID = "2"


def _esc_attr(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _toc_style(level):
    ind = f'<w:ind w:left="{220 * (level - 1)}"/>' if level > 1 else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="TOC{level}"><w:name w:val="toc {level}"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        f'<w:pPr><w:spacing w:after="100"/>{ind}</w:pPr></w:style>'
    )


def _heading_style(level, size):
    return (
        f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
        f'<w:pPr><w:keepNext/><w:spacing w:before="{240 if level <= 2 else 160}" w:after="120"/>'
        f'<w:outlineLvl w:val="{level - 1}"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:style>'
    )


def _styles_xml(east_asia=None):
    ea = f' w:eastAsia="{_esc_attr(str(east_asia))}"' if east_asia else ""
    return (
        XML_DECL
        + '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        f'<w:rFonts w:ascii="Times New Roman"{ea} w:hAnsi="Times New Roman"/><w:sz w:val="28"/><w:szCs w:val="28"/>'
        "</w:rPr></w:rPrDefault>"
        '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        + _heading_style(1, 32)
        + _heading_style(2, 28)
        + _heading_style(3, 26)
        + _heading_style(4, 24)
        + _heading_style(5, 22)
        + _heading_style(6, 22)
        + '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>'
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
        + "".join(_toc_style(i + 1) for i in range(9))
        + "</w:styles>"
    )


def _bullet_levels():
    x = ""
    for ilvl in range(5):
        x += (
            f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#61623;"/>'
            f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (ilvl + 1)}" w:hanging="360"/></w:pPr>'
            '<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr></w:lvl>'
        )
    return x


def _decimal_levels():
    x = ""
    for ilvl in range(5):
        x += (
            f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%{ilvl + 1}."/>'
            f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (ilvl + 1)}" w:hanging="360"/></w:pPr></w:lvl>'
        )
    return x


@dataclass
class CustomNumberingLevel:
    num_fmt: str
    lvl_text: str
    indent_left: int
    hanging: int = 360
    start: int = 1


def _custom_levels(levels):
    def value(level, snake, camel=None, default=None):
        if isinstance(level, dict):
            if snake in level:
                return level[snake]
            if camel and camel in level:
                return level[camel]
            return default
        return getattr(level, snake, default)

    parts = []
    for i, level in enumerate(levels):
        if i > 8:
            break
        try:
            start = int(value(level, "start", default=1))
            indent = int(value(level, "indent_left", "indentLeft", 720 * (i + 1)))
            hanging = int(value(level, "hanging", default=360))
        except (TypeError, ValueError) as exc:
            raise ValueError("custom numbering start/indent/hanging must be integers") from exc
        if start < 0 or indent < 0 or hanging < 0:
            raise ValueError("custom numbering start/indent/hanging must be non-negative")
        num_fmt = _esc_attr(str(value(level, "num_fmt", "numFmt", "decimal")))
        lvl_text = _esc_attr(str(value(level, "lvl_text", "lvlText", f"%{i + 1}.")))
        parts.append(
            f'<w:lvl w:ilvl="{i}"><w:start w:val="{start}"/>'
            f'<w:numFmt w:val="{num_fmt}"/><w:lvlText w:val="{lvl_text}"/>'
            f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{indent}" w:hanging="{hanging}"/></w:pPr></w:lvl>'
        )
    return "".join(parts)


def abstract_num_xml(abstract_num_id: str, kind: str, levels=None) -> str:
    try:
        abstract_id = int(abstract_num_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("abstractNumId must be a non-negative integer") from exc
    if abstract_id < 0:
        raise ValueError("abstractNumId must be a non-negative integer")
    kind = str(kind)
    if kind not in {"bullet", "ordered", "decimal"}:
        raise ValueError("numbering kind must be bullet or ordered")
    body = (
        _custom_levels(levels)
        if levels
        else (_bullet_levels() if kind == "bullet" else _decimal_levels())
    )
    return f'<w:abstractNum w:abstractNumId="{abstract_id}">{body}</w:abstractNum>'


BLANK_NUMBERING_XML = (
    XML_DECL
    + '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    f'<w:abstractNum w:abstractNumId="0">{_bullet_levels()}</w:abstractNum>'
    f'<w:abstractNum w:abstractNumId="1">{_decimal_levels()}</w:abstractNum>'
    f'<w:num w:numId="{BLANK_BULLET_NUM_ID}"><w:abstractNumId w:val="0"/></w:num>'
    f'<w:num w:numId="{BLANK_ORDERED_NUM_ID}"><w:abstractNumId w:val="1"/></w:num>'
    "</w:numbering>"
)


def build_blank_docx(east_asia_font: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            XML_DECL
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/_rels/document.xml.rels",
            XML_DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
            "</Relationships>",
        )
        z.writestr("word/styles.xml", _styles_xml(east_asia_font))
        z.writestr("word/numbering.xml", BLANK_NUMBERING_XML)
        sect = (
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
            "</w:sectPr>"
        )
        z.writestr(
            "word/document.xml",
            XML_DECL + f"<w:document {DOC_NS}><w:body><w:p/>{sect}</w:body></w:document>",
        )
    return buf.getvalue()
