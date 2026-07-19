"""Генератор reference.docx со стилями по умолчанию для create_docx.

Дефолты:
  - Шрифт: Times New Roman 14pt (через document defaults — перекрывает тему)
  - Межстрочный интервал: 1.5
  - Заголовки H1-H6: TNR, чёрные, жирные
  - Отступ первой строки абзаца: 1.25 cm
  - Поля: 2 cm со всех сторон

Файл генерируется при первом запуске и кэшируется в `.data/docx_reference.docx`.
Для регенерации — удалить вручную.
"""

from pathlib import Path

from logger import logger

_REFERENCE_FILENAME = "docx_reference.docx"
_BASE_FONT = "Times New Roman"
_CODE_FONT = "Courier New"


def _data_dir() -> Path:
    from config.paths import BASE_DIR
    p = Path(BASE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_default_reference_path() -> Path:
    ref = _data_dir() / _REFERENCE_FILENAME
    if ref.exists() and ref.stat().st_size > 0:
        return ref
    _build_reference(ref)
    return ref


def _build_reference(out_path: Path) -> None:
    """Строит reference.docx через python-docx с нужными стилями."""
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()

    for section in doc.sections:
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(2)
        section.right_margin = Cm(2)

    base_size = Pt(14)
    black = RGBColor(0x00, 0x00, 0x00)

    _force_doc_defaults(doc, _BASE_FONT, base_size, black)
    _override_theme_fonts(doc, _BASE_FONT)

    def _set_rfonts(style_el, font_name: str) -> None:
        rpr = style_el.get_or_add_rPr()
        for old in rpr.findall(qn("w:rFonts")):
            rpr.remove(old)
        rfonts = OxmlElement("w:rFonts")
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), font_name)
        rpr.append(rfonts)

    def _apply_font(style, *, size=base_size, bold=False, color=black, font_name=_BASE_FONT):
        f = style.font
        f.name = font_name
        f.size = size
        f.bold = bold
        f.color.rgb = color
        _set_rfonts(style.element, font_name)

    def _apply_paragraph(style, *, first_line_indent=None, space_before=Pt(0), space_after=Pt(0), align=None):
        pf = style.paragraph_format
        pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        pf.line_spacing = 1.5
        pf.space_before = space_before
        pf.space_after = space_after
        if first_line_indent is not None:
            pf.first_line_indent = first_line_indent
        if align is not None:
            pf.alignment = align

    normal = doc.styles["Normal"]
    _apply_font(normal, size=base_size, bold=False, color=black)
    _apply_paragraph(normal, first_line_indent=Cm(1.25))

    heading_specs = {
        "Heading 1": Pt(16),
        "Heading 2": Pt(14),
        "Heading 3": Pt(13),
        "Heading 4": Pt(13),
        "Heading 5": Pt(13),
        "Heading 6": Pt(13),
        "Title": Pt(20),
        "Subtitle": Pt(16),
    }
    for name, size in heading_specs.items():
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        _apply_font(st, size=size, bold=True, color=black)
        _apply_paragraph(st, first_line_indent=Cm(0), space_before=Pt(12), space_after=Pt(6))
        # Также проставляем шрифт в *_Char стилях (используются для run-level)
        char_name = f"{name} Char"
        try:
            char_st = doc.styles[char_name]
        except KeyError:
            char_st = None
        if char_st is not None:
            cf = char_st.font
            cf.name = _BASE_FONT
            cf.size = size
            cf.bold = True
            cf.color.rgb = black
            _set_rfonts(char_st.element, _BASE_FONT)

    for list_style in ("List Bullet", "List Number", "List Paragraph"):
        try:
            st = doc.styles[list_style]
        except KeyError:
            continue
        _apply_font(st, size=base_size, bold=False, color=black)
        _apply_paragraph(st, first_line_indent=Cm(0))

    for q_style in ("Quote", "Intense Quote"):
        try:
            st = doc.styles[q_style]
        except KeyError:
            continue
        _apply_font(st, size=base_size, bold=False, color=black)
        _apply_paragraph(st, first_line_indent=Cm(0))

    try:
        code_style = doc.styles["Source Code"]
    except KeyError:
        code_style = None
    if code_style is not None:
        f = code_style.font
        f.name = _CODE_FONT
        f.size = Pt(12)
        f.color.rgb = black
        _set_rfonts(code_style.element, _CODE_FONT)
        pf = code_style.paragraph_format
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
        pf.first_line_indent = Cm(0)

    for style_name in ("Body Text", "First Paragraph", "Compact"):
        try:
            st = doc.styles[style_name]
        except KeyError:
            continue
        _apply_font(st, size=base_size, bold=False, color=black)
        _apply_paragraph(st, first_line_indent=Cm(1.25))

    _add_centered_style(doc, _BASE_FONT, base_size, black)
    _add_right_style(doc, _BASE_FONT, base_size, black)
    _add_justify_style(doc, _BASE_FONT, base_size, black)
    _setup_table_style(doc, _BASE_FONT, base_size, black)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    logger.info("docx_reference: built {} (TNR 14, line=1.5, black headings)", out_path)


def _force_doc_defaults(doc, font_name: str, size_pt, color_rgb) -> None:
    """Устанавливает rFonts и sz в w:docDefaults — это перебивает тему Calibri."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    styles_el = doc.styles.element
    docDefaults = styles_el.find(qn("w:docDefaults"))  # noqa: N806
    if docDefaults is None:
        docDefaults = OxmlElement("w:docDefaults")  # noqa: N806
        styles_el.insert(0, docDefaults)

    rPrDefault = docDefaults.find(qn("w:rPrDefault"))  # noqa: N806
    if rPrDefault is None:
        rPrDefault = OxmlElement("w:rPrDefault")  # noqa: N806
        docDefaults.append(rPrDefault)
    rPr = rPrDefault.find(qn("w:rPr"))  # noqa: N806
    if rPr is None:
        rPr = OxmlElement("w:rPr")  # noqa: N806
        rPrDefault.append(rPr)
    for old in rPr.findall(qn("w:rFonts")):
        rPr.remove(old)
    rfonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), font_name)
    rPr.append(rfonts)
    for old in rPr.findall(qn("w:sz")):
        rPr.remove(old)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size_pt.pt * 2)))
    rPr.append(sz)
    szCs = OxmlElement("w:szCs")  # noqa: N806
    szCs.set(qn("w:val"), str(int(size_pt.pt * 2)))
    rPr.append(szCs)
    for old in rPr.findall(qn("w:color")):
        rPr.remove(old)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), f"{color_rgb[0]:02X}{color_rgb[1]:02X}{color_rgb[2]:02X}")
    rPr.append(color)

    pPrDefault = docDefaults.find(qn("w:pPrDefault"))  # noqa: N806
    if pPrDefault is None:
        pPrDefault = OxmlElement("w:pPrDefault")  # noqa: N806
        docDefaults.append(pPrDefault)
    pPr = pPrDefault.find(qn("w:pPr"))  # noqa: N806
    if pPr is None:
        pPr = OxmlElement("w:pPr")  # noqa: N806
        pPrDefault.append(pPr)
    for old in pPr.findall(qn("w:spacing")):
        pPr.remove(old)
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:line"), "360")
    spacing.set(qn("w:lineRule"), "auto")
    spacing.set(qn("w:after"), "0")
    spacing.set(qn("w:before"), "0")
    pPr.append(spacing)


def _override_theme_fonts(doc, font_name: str) -> None:
    """Подменяет шрифты в word/theme/theme1.xml на нужный — иначе LibreOffice/Word
    могут показывать Calibri, даже если в стиле задан TNR (наследуется через тему).
    """
    import re as _re

    try:
        theme_part = doc.part.part_related_by(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme",
        )
    except Exception:
        logger.debug("docx_reference: no theme part to override fonts", exc_info=True)
        return
    if theme_part is None:
        return
    xml = theme_part.blob.decode("utf-8")
    xml = _re.sub(
        r'(<a:latin\s+typeface=")[^"]+(")',
        rf'\1{font_name}\2',
        xml,
    )
    xml = _re.sub(
        r'(<a:ea\s+typeface=")[^"]*(")',
        rf'\1{font_name}\2',
        xml,
    )
    xml = _re.sub(
        r'(<a:cs\s+typeface=")[^"]*(")',
        rf'\1{font_name}\2',
        xml,
    )
    # python-docx не даёт публичного API для записи XML части темы; пишем
    # напрямую в приватный _blob (зависимость от внутренней реализации
    # python-docx — может сломаться при мажорном обновлении пакета).
    theme_part._blob = xml.encode("utf-8")


def _add_centered_style(doc, font_name, size, color) -> None:
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    name = "Centered"
    if name in [s.name for s in doc.styles]:
        return
    st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    f = st.font
    f.name = font_name
    f.size = size
    f.color.rgb = color
    pf = st.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.first_line_indent = Cm(0)
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = 1.5
    rpr = st.element.get_or_add_rPr()
    rfonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), font_name)
    rpr.append(rfonts)


def _add_right_style(doc, font_name, size, color) -> None:
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    name = "RightAligned"
    if name in [s.name for s in doc.styles]:
        return
    st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    f = st.font
    f.name = font_name
    f.size = size
    f.color.rgb = color
    pf = st.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    pf.first_line_indent = Cm(0)
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = 1.5
    rpr = st.element.get_or_add_rPr()
    rfonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), font_name)
    rpr.append(rfonts)


def _setup_table_style(doc, font_name, size, color) -> None:
    """Настраивает стиль 'Table' (используется pandoc по умолчанию для всех таблиц):
    одинарные тонкие границы, паддинги, TNR, без отступа первой строки в ячейках.
    """
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm
    from docx.shared import Pt as _Pt

    name = "Table"
    existing = [s.name for s in doc.styles]
    st = doc.styles[name] if name in existing else doc.styles.add_style(name, WD_STYLE_TYPE.TABLE)

    f = st.font
    f.name = font_name
    f.size = size
    f.color.rgb = color
    rpr = st.element.get_or_add_rPr()
    for old in rpr.findall(qn("w:rFonts")):
        rpr.remove(old)
    rfonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), font_name)
    rpr.append(rfonts)

    pf = st.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.first_line_indent = Cm(0)
    pf.space_before = _Pt(0)
    pf.space_after = _Pt(0)

    st_el = st.element
    tblPr = st_el.find(qn("w:tblPr"))  # noqa: N806
    if tblPr is None:
        tblPr = OxmlElement("w:tblPr")  # noqa: N806
        st_el.append(tblPr)

    for old in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(old)
    tblBorders = OxmlElement("w:tblBorders")  # noqa: N806
    for border_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{border_name}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "000000")
        tblBorders.append(b)
    tblPr.append(tblBorders)

    for old in tblPr.findall(qn("w:tblCellMar")):
        tblPr.remove(old)
    cellMar = OxmlElement("w:tblCellMar")  # noqa: N806
    for side, val in (("top", "60"), ("left", "108"), ("bottom", "60"), ("right", "108")):
        s = OxmlElement(f"w:{side}")
        s.set(qn("w:w"), val)
        s.set(qn("w:type"), "dxa")
        cellMar.append(s)
    tblPr.append(cellMar)


def _add_justify_style(doc, font_name, size, color) -> None:
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    name = "Justified"
    if name in [s.name for s in doc.styles]:
        return
    st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    f = st.font
    f.name = font_name
    f.size = size
    f.color.rgb = color
    pf = st.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf.first_line_indent = Cm(1.25)
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = 1.5
    rpr = st.element.get_or_add_rPr()
    rfonts = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), font_name)
    rpr.append(rfonts)
