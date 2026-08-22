"""OOXML XML helpers with no non-stdlib XML dependency."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from html import escape
from xml.etree import ElementTree as ET

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"

NS = {"p": P_NS, "a": A_NS, "r": R_NS, "c": C_NS, "dgm": DML_NS}
# OOXML parts never need DTDs or custom entities. Rejecting them before parsing
# prevents entity-expansion payloads from consuming process resources.
_UNSAFE_XML_DECLARATION_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def qn(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_escape_text(value: str) -> str:
    return escape(str(value), quote=False)


def xml_escape_attr(value: str) -> str:
    return escape(str(value), quote=True)


def parse_xml(xml: str) -> ET.Element:
    """Parse an OOXML fragment while rejecting DTD and entity declarations."""
    if _UNSAFE_XML_DECLARATION_RE.search(xml):
        raise ET.ParseError("DTD and entity declarations are not allowed in OOXML")
    return ET.fromstring(xml.encode("utf-8"))


def find_first(element: ET.Element, path: str) -> ET.Element | None:
    return element.find(path, NS)


def find_all(element: ET.Element, path: str) -> list[ET.Element]:
    return list(element.findall(path, NS))


def attr_int(element: ET.Element | None, key: str, default: int = 0) -> int:
    if element is None:
        return default
    try:
        return int(element.get(key, str(default)))
    except ValueError:
        return default


def attr_float(element: ET.Element | None, key: str, default: float = 0.0) -> float:
    if element is None:
        return default
    try:
        return float(element.get(key, str(default)))
    except ValueError:
        return default


def tag_ranges(
    xml: str, tag_names: Iterable[str], start: int = 0, end: int | None = None
) -> list[tuple[str, int, int]]:
    """Return top-level ranges for named prefixed tags by scanning XML tokens.

    This scanner deliberately operates on raw text: it gives exact slices needed
    for byte-preserving save and works for self-closing shapes and normal nodes.
    It is used only on the direct content of ``p:spTree`` or ``p:grpSp``.
    """
    wanted = set(tag_names)
    limit = len(xml) if end is None else end
    token_re = re.compile(
        r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<[^>]+>", re.DOTALL
    )
    result: list[tuple[str, int, int]] = []
    stack: list[tuple[str, int]] = []
    for match in token_re.finditer(xml, start, limit):
        token = match.group(0)
        if token.startswith(("<!--", "<?", "<!")):
            continue
        closing = token.startswith("</")
        name_m = re.match(r"</?\s*([\w.-]+:[\w.-]+|[\w.-]+)", token)
        if not name_m:
            continue
        name = name_m.group(1)
        self_closing = token.rstrip().endswith("/>")
        if closing:
            if stack:
                open_name, open_at = stack.pop()
                if open_name == name and name in wanted and not stack:
                    result.append((name, open_at, match.end()))
            continue
        if name in wanted and not stack:
            if self_closing:
                result.append((name, match.start(), match.end()))
            else:
                stack.append((name, match.start()))
        elif stack and not self_closing:
            stack.append((name, match.start()))
    return result


def element_range(
    xml: str, open_tag: str, from_index: int = 0
) -> tuple[int, int] | None:
    """Find an element's exact range, including nested same-name nodes."""
    m = re.search(rf"<{re.escape(open_tag)}\b[^>]*>", xml[from_index:])
    if not m:
        m = re.search(rf"<{re.escape(open_tag)}\b[^>]*/>", xml[from_index:])
        if not m:
            return None
        return from_index + m.start(), from_index + m.end()
    start = from_index + m.start()
    raw = m.group(0)
    if raw.rstrip().endswith("/>"):
        return start, from_index + m.end()
    token_re = re.compile(rf"</?{re.escape(open_tag)}\b[^>]*>", re.DOTALL)
    depth = 0
    for x in token_re.finditer(xml, start):
        token = x.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return start, x.end()
        elif not token.rstrip().endswith("/>"):
            depth += 1
    return None


def first_child_range(
    xml: str, container_tag: str, direct_tags: Iterable[str]
) -> tuple[int, int] | None:
    rng = element_range(xml, container_tag)
    if not rng:
        return None
    ranges = tag_ranges(xml, direct_tags, rng[0], rng[1])
    return (ranges[0][1], ranges[-1][2]) if ranges else None


def replace_or_insert_child(
    xml: str, container_tag: str, child_match: re.Pattern[str], child_xml: str
) -> str:
    """Replace a direct XML child (regular-expression target), or append it to container."""
    container = element_range(xml, container_tag)
    if not container:
        return xml
    body = xml[container[0] : container[1]]
    hit = child_match.search(body)
    if hit:
        body = body[: hit.start()] + child_xml + body[hit.end() :]
    else:
        close = body.rfind(f"</{container_tag}>")
        if close < 0:
            return xml
        body = body[:close] + child_xml + body[close:]
    return xml[: container[0]] + body + xml[container[1] :]


def colour_from_node(
    node: ET.Element | None, theme: dict[str, str], default: str = "000000"
) -> str:
    """Resolve common DrawingML colour nodes to #RRGGBB[AA]."""
    if node is None:
        return f"#{default.upper()}"
    color = None
    for child in node.iter():
        name = local(child.tag)
        if name == "srgbClr":
            color = child.get("val")
        elif name == "sysClr":
            color = child.get("lastClr") or child.get("val")
        elif name == "schemeClr":
            color = theme.get(child.get("val", ""), default)
        elif name == "prstClr":
            color = child.get("val")
        if color:
            break
    if not color:
        return f"#{default.upper()}"
    color = color.lstrip("#")
    alpha = None
    for child in node.iter():
        if local(child.tag) in {"alpha", "alphaMod", "alphaOff"} and child.get("val"):
            with contextlib.suppress(ValueError):
                alpha = round(int(child.get("val", "100000")) * 255 / 100000)
    return (
        "#"
        + color.upper()
        + (f"{alpha:02X}" if alpha is not None and alpha < 255 else "")
    )


def hex_to_rgba(color: str | None, opacity: float = 1.0) -> tuple[int, int, int, int]:
    value = (color or "#000000").lstrip("#")
    if value.lower() == "none":
        return 0, 0, 0, 0
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    # Six-digit RGB is opaque. Only an explicitly supplied 8-digit value
    # carries its own alpha channel.
    explicit_alpha = len(value) >= 8
    value = (value + "000000")[:8] if explicit_alpha else value[:6]
    try:
        r, g, b = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        a = int(value[6:8], 16) if explicit_alpha else 255
    except ValueError:
        return 0, 0, 0, 255
    return r, g, b, round(a * max(0.0, min(1.0, opacity)))
