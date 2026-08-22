"""Порядок-сохраняющий XML DOM (preserveOrder-стиль)."""

from __future__ import annotations

import re
from xml.parsers import expat

XNode = dict  # {name: [child,...], ':@': {attrs}}  либо {'#text': str}

ILLEGAL_XML_CHARS = re.compile(
    r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\uD800-\uDFFF\uFFFE\uFFFF]"
)


def parse(xml: str) -> list[XNode]:
    roots: list[XNode] = []
    stack: list[XNode] = []
    names: list[str] = []
    p = expat.ParserCreate()
    p.ordered_attributes = True

    def start(name, attrs):
        node: XNode = {name: []}
        if attrs:
            node[":@"] = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs), 2)}
        if stack:
            stack[-1][names[-1]].append(node)
        else:
            roots.append(node)
        stack.append(node)
        names.append(name)

    def end(_name):
        stack.pop()
        names.pop()

    def chars(data):
        if stack:
            stack[-1][names[-1]].append({"#text": data})

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    p.Parse(xml, True)
    return roots


def name_of(node: XNode) -> str | None:
    for k in node:
        if k not in (":@", "#text"):
            return k
    return None


def children_of(node: XNode) -> list[XNode]:
    n = name_of(node)
    if not n:
        return []
    v = node.get(n)
    return v if isinstance(v, list) else []


def attrs_of(node: XNode) -> dict[str, str]:
    return node.get(":@") or {}


def text_of(node: XNode) -> str:
    out = []
    for c in children_of(node):
        if "#text" in c:
            out.append(str(c["#text"]))
        else:
            out.append(text_of(c))
    return "".join(out)


def find_child(node: XNode, name: str) -> XNode | None:
    for c in children_of(node):
        if name_of(c) == name:
            return c
    return None


def find_children(node: XNode, name: str) -> list[XNode]:
    return [c for c in children_of(node) if name_of(c) == name]


def children_through_sdt(node: XNode, name: str) -> list[XNode]:
    out: list[XNode] = []

    def visit(n: XNode):
        for c in children_of(n):
            cn = name_of(c)
            if cn == name:
                out.append(c)
            elif cn == "w:sdt":
                content = find_child(c, "w:sdtContent")
                if content:
                    visit(content)

    visit(node)
    return out


def bool_prop(parent: XNode, name: str) -> bool:
    child = find_child(parent, name)
    if not child:
        return False
    val = attrs_of(child).get("w:val")
    if val is None:
        return True
    return str(val).lower() not in ("0", "false", "none", "off")


def underline_prop(parent: XNode) -> bool:
    child = find_child(parent, "w:u")
    if not child:
        return False
    val = attrs_of(child).get("w:val")
    # An empty <w:u/> means the default underline style (single).
    return val is None or str(val).lower() not in ("none", "0", "false", "off")


def unescape_xml_text(text: str) -> str:
    """Decode one layer of XML character/entity references from raw XML text."""
    text = str(text)

    def numeric(m):
        raw = m.group(1)
        try:
            cp = int(raw[1:], 16) if raw[:1].lower() == "x" else int(raw, 10)
        except ValueError:
            return m.group(0)
        if not (0 <= cp <= 0x10FFFF) or 0xD800 <= cp <= 0xDFFF:
            return m.group(0)
        try:
            return chr(cp)
        except ValueError:
            return m.group(0)

    text = re.sub(r"&#(x[0-9A-Fa-f]+|[0-9]+);", numeric, text)
    # Decode exactly one XML layer: ampersand is intentionally last.
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def _validated_xml_text(text: str) -> str:
    text = str(text)
    m = ILLEGAL_XML_CHARS.search(text)
    if m:
        raise ValueError(
            f"text contains XML-illegal control character U+{ord(m.group(0)):04X}"
        )
    return text


def escape_xml_text(text: str) -> str:
    text = _validated_xml_text(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_xml_attr(text: str) -> str:
    # XML processors normalize literal TAB/LF/CR in attributes. Numeric
    # references preserve the exact characters across parse/serialize cycles.
    return (
        escape_xml_text(text)
        .replace('"', "&quot;")
        .replace("\t", "&#9;")
        .replace("\n", "&#10;")
        .replace("\r", "&#13;")
    )


def serialize_xnode(node: XNode) -> str:
    if "#text" in node:
        return escape_xml_text(str(node["#text"]))
    n = name_of(node)
    if not n:
        return ""
    attrs = "".join(
        f' {k}="{escape_xml_attr(str(v))}"' for k, v in attrs_of(node).items()
    )
    inner = "".join(serialize_xnode(c) for c in children_of(node))
    if inner == "":
        return f"<{n}{attrs}/>"
    return f"<{n}{attrs}>{inner}</{n}>"
