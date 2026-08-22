"""word/theme/theme1.xml."""

from __future__ import annotations

import re

from .xml_utils import escape_xml_attr

THEME_PART_PATH = "word/theme/theme1.xml"
THEME_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
)
THEME_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.theme+xml"

COLOR_TAGS = [
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
]
READ_TAGS = ["dk1", "lt1", *COLOR_TAGS, "hlink", "folHlink"]
SLOTS = {
    "dark1": "dk1",
    "text1": "dk1",
    "light1": "lt1",
    "background1": "lt1",
    "dark2": "dk2",
    "text2": "dk2",
    "light2": "lt2",
    "background2": "lt2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}
FALLBACK = {"dk1": "000000", "lt1": "FFFFFF"}
_HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")


def _section(xml: str, tag: str):
    m = re.search(rf"<{tag}(?:\s[^>]*)?>[\s\S]*?</{tag}>", xml)
    return m.group(0) if m else None


def _hex_color(value, *, field: str) -> str:
    value = str(value).strip()
    if not _HEX6.fullmatch(value):
        raise ValueError(f"{field} must be a 6-digit RGB hex color")
    return value.upper()


def read_theme_fonts(xml: str):
    major = minor = None
    for tag, key in (("a:majorFont", "major"), ("a:minorFont", "minor")):
        sec = _section(xml, tag)
        if sec:
            m = re.search(r'<a:latin typeface="([^"]*)"', sec)
            if key == "major":
                major = m.group(1) if m else None
            else:
                minor = m.group(1) if m else None
    if major is None and minor is None:
        return None
    out = {"major": major or "", "minor": minor or ""}
    sec = _section(xml, "a:minorFont")
    if sec:
        ea = re.search(r'<a:ea typeface="([^"]*)"', sec)
        if ea and ea.group(1):
            out["eastAsia"] = ea.group(1)
    return out


def apply_theme_fonts(xml: str, fonts: dict) -> str:
    if not isinstance(fonts, dict):
        raise ValueError("theme fonts must be an object")
    for tag, key in (("a:majorFont", "major"), ("a:minorFont", "minor")):
        sec = _section(xml, tag)
        if not sec or key not in fonts:
            continue
        nxt = re.sub(
            r'(<a:latin typeface=")[^"]*(")',
            lambda m, _key=key: (
                m.group(1) + escape_xml_attr(fonts.get(_key, "")) + m.group(2)
            ),
            sec,
        )
        if fonts.get("eastAsia") is not None:
            nxt = re.sub(
                r'(<a:ea typeface=")[^"]*(")',
                lambda m: m.group(1) + escape_xml_attr(fonts["eastAsia"]) + m.group(2),
                nxt,
            )
        xml = xml.replace(sec, nxt)
    return xml


def read_theme_colors(xml: str):
    scheme = _section(xml, "a:clrScheme")
    if not scheme:
        return None
    out = {}
    nm = re.search(r'<a:clrScheme name="([^"]*)"', scheme)
    if nm:
        out["name"] = nm.group(1)
    for tag in READ_TAGS:
        m = re.search(
            rf"<a:{tag}>\s*<a:(?:srgbClr val|sysClr[^>]*? lastClr)=\"([0-9A-Fa-f]{{6}})\"",
            scheme,
        )
        if m:
            out[tag] = m.group(1).upper()
    return out or None


def resolve_theme_color(theme_color: str, colors: dict, tint=None, shade=None):
    slot = SLOTS.get(theme_color)
    if not slot:
        return None
    base = colors.get(slot) or FALLBACK.get(slot)
    if not base or not _HEX6.fullmatch(str(base)):
        return None
    rgb = [int(str(base)[i : i + 2], 16) for i in (0, 2, 4)]

    def factor(hexc):
        if hexc is None or hexc == "":
            return None
        try:
            # OOXML tint/shade is a single byte, not an arbitrary-length hex integer.
            if not re.fullmatch(r"[0-9A-Fa-f]{1,2}", str(hexc)):
                return None
            return int(str(hexc), 16) / 255
        except ValueError:
            return None

    s = factor(shade)
    if s is not None:
        rgb = [c * s for c in rgb]
    t = factor(tint)
    if t is not None:
        rgb = [c * t + 255 * (1 - t) for c in rgb]
    return "".join(format(max(0, min(255, round(c))), "02X") for c in rgb)


def apply_theme_colors(xml: str, colors: dict) -> str:
    if not isinstance(colors, dict):
        raise ValueError("theme colors must be an object")
    scheme = _section(xml, "a:clrScheme")
    if not scheme:
        return xml
    original = scheme
    for tag in COLOR_TAGS:
        v = colors.get(tag)
        if v is None or v == "":
            continue
        v = _hex_color(v, field=tag)
        scheme = re.sub(
            rf'(<a:{tag}>\s*<a:srgbClr val=")[0-9A-Fa-f]{{6}}(")',
            lambda m, value=v: m.group(1) + value + m.group(2),
            scheme,
        )
    xml = xml.replace(original, scheme)
    if colors.get("name") is not None:
        xml = re.sub(
            r'(<a:clrScheme name=")[^"]*(")',
            lambda m: m.group(1) + escape_xml_attr(colors["name"]) + m.group(2),
            xml,
        )
    return xml


def build_theme_xml(fonts: dict, colors: dict) -> str:
    fonts = fonts or {}
    colors = colors or {}
    major = str(fonts.get("major") or "Times New Roman")
    minor = str(fonts.get("minor") or "Times New Roman")

    def c(tag, fb):
        raw = colors.get(tag)
        value = _hex_color(raw, field=tag) if raw not in (None, "") else fb
        return f'<a:{tag}><a:srgbClr val="{value}"/></a:{tag}>'

    def fnt(tag, tf):
        return (
            f'<{tag}><a:latin typeface="{escape_xml_attr(tf)}"/>'
            f'<a:ea typeface="{escape_xml_attr(fonts.get("eastAsia") or "")}"/><a:cs typeface=""/></{tag}>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office">'
        "<a:themeElements>"
        f'<a:clrScheme name="{escape_xml_attr(colors.get("name") or "Office")}">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        + c("dk2", "44546A")
        + c("lt2", "E7E6E6")
        + c("accent1", "4472C4")
        + c("accent2", "ED7D31")
        + c("accent3", "A5A5A5")
        + c("accent4", "FFC000")
        + c("accent5", "5B9BD5")
        + c("accent6", "70AD47")
        + '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        "</a:clrScheme>"
        f'<a:fontScheme name="Office">{fnt("a:majorFont", major)}{fnt("a:minorFont", minor)}</a:fontScheme>'
        '<a:fmtScheme name="Office">'
        '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
        "<a:lnStyleLst>"
        '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        "</a:lnStyleLst>"
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
        "</a:fmtScheme>"
        "</a:themeElements></a:theme>"
    )
