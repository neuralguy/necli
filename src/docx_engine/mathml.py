"""OMML <-> MathML <-> LaTeX-подмножество."""

from __future__ import annotations

import re
import unicodedata

from .xml_utils import (
    attrs_of,
    children_of,
    escape_xml_attr,
    escape_xml_text,
    find_child,
    name_of,
    text_of,
    unescape_xml_text,
)
from .xml_utils import parse as xml_parse

# ---------- фрагменты / токены ----------
_OMML_RE = re.compile(r"<m:oMath(?=[\s>])(?:\s[^>]*)?>[\s\S]*?</m:oMath>")
_MT_RE = re.compile(r"<m:t(?:\s[^>]*)?>([\s\S]*?)</m:t>")


def omml_fragments_of(paragraph_xml: str) -> list[str]:
    return _OMML_RE.findall(paragraph_xml)


def math_tokens_of(xml: str) -> list[str]:
    return [unescape_xml_text(m) for m in _MT_RE.findall(xml)]


# ---------- OMML -> MathML ----------
def _content_children(node):
    return [c for c in children_of(node) if name_of(c) and not name_of(c).endswith("Pr")]


def _mml_children(nodes):
    return "".join(_mml_of(n) for n in nodes)


def _mml_slot(parent, name):
    slot = find_child(parent, name)
    if not slot:
        return "<mrow></mrow>"
    return f"<mrow>{_mml_children(_content_children(slot))}</mrow>"


def _prop_val(node, pr, child):
    p = find_child(node, pr)
    if not p:
        return None
    c = find_child(p, child)
    return attrs_of(c).get("m:val") if c else None


def _prop_on(node, pr, child):
    v = _prop_val(node, pr, child)
    return v is not None and str(v).lower() not in ("0", "false", "off")


def _mo(ch, extra=""):
    return f"<mo{extra}>{escape_xml_text(ch)}</mo>"


OPERATOR_CHARS = set("+-−=<>±∓×÷·⋅∙*/!%&|,;:()[]{}′″∞→←↔⇒⇐⇔∈∉⊂⊃∪∩∀∃∧∨¬≤≥≠≈≡∼∝⊥∥°∂∇")


def _run_text_to_mml(text, plain):
    if plain:
        return "" if text == "" else f"<mi>{escape_xml_text(text)}</mi>"
    out, i, chars = "", 0, list(text)
    while i < len(chars):
        ch = chars[i]
        if ch in "0123456789.":
            num = ""
            while i < len(chars) and chars[i] in "0123456789.":
                num += chars[i]
                i += 1
            out += f"<mn>{num}</mn>"
        elif (ch.isascii() and ch.isalpha()) or "\u0370" <= ch <= "\u03ff":
            out += f"<mi>{escape_xml_text(ch)}</mi>"
            i += 1
        elif ch == " ":
            i += 1
        elif ch in OPERATOR_CHARS:
            out += _mo(ch, ' stretchy="false"') if ch in "()[]{}|" else _mo(ch)
            i += 1
        else:
            out += f"<mtext>{escape_xml_text(ch)}</mtext>"
            i += 1
    return out


def _run_to_mml(run):
    sty = _prop_val(run, "m:rPr", "m:sty")
    plain = sty == "p" or find_child(find_child(run, "m:rPr") or {}, "m:nor") is not None
    return "".join(
        _run_text_to_mml(text_of(c), plain) for c in children_of(run) if name_of(c) == "m:t"
    )


def _mml_of(node):
    n = name_of(node)
    if n == "m:r":
        return _run_to_mml(node)
    if n == "m:f":
        t = _prop_val(node, "m:fPr", "m:type")
        attrs = (
            ' linethickness="0"'
            if t == "noBar"
            else (' bevelled="true"' if t in ("lin", "skw") else "")
        )
        return f"<mfrac{attrs}>{_mml_slot(node, 'm:num')}{_mml_slot(node, 'm:den')}</mfrac>"
    if n == "m:sSup":
        return f"<msup>{_mml_slot(node, 'm:e')}{_mml_slot(node, 'm:sup')}</msup>"
    if n == "m:sSub":
        return f"<msub>{_mml_slot(node, 'm:e')}{_mml_slot(node, 'm:sub')}</msub>"
    if n == "m:sSubSup":
        return f"<msubsup>{_mml_slot(node, 'm:e')}{_mml_slot(node, 'm:sub')}{_mml_slot(node, 'm:sup')}</msubsup>"
    if n == "m:rad":
        inner = _mml_slot(node, "m:e")
        if _prop_on(node, "m:radPr", "m:degHide") or not find_child(node, "m:deg"):
            return f"<msqrt>{inner}</msqrt>"
        return f"<mroot>{inner}{_mml_slot(node, 'm:deg')}</mroot>"
    if n == "m:d":
        beg = _prop_val(node, "m:dPr", "m:begChr") or "("
        end = _prop_val(node, "m:dPr", "m:endChr") or ")"
        sep = _prop_val(node, "m:dPr", "m:sepChr") or "|"
        slots = [c for c in children_of(node) if name_of(c) == "m:e"]
        body = ((sep and _mo(sep)) or "").join(
            f"<mrow>{_mml_children(_content_children(s))}</mrow>" for s in slots
        )
        stretchy = ' stretchy="true"'
        return f"<mrow>{_mo(beg, stretchy) if beg else ''}{body}{_mo(end, stretchy) if end else ''}</mrow>"
    if n == "m:nary":
        chr_ = _prop_val(node, "m:naryPr", "m:chr") or "\u222b"
        lim = _prop_val(node, "m:naryPr", "m:limLoc") or (
            "subSup" if chr_ == "\u222b" else "undOvr"
        )
        sub_h, sup_h = (
            _prop_on(node, "m:naryPr", "m:subHide"),
            _prop_on(node, "m:naryPr", "m:supHide"),
        )
        op = _mo(chr_, ' stretchy="false"')
        if not sub_h and not sup_h:
            tag = "munderover" if lim == "undOvr" else "msubsup"
            scripted = f"<{tag}>{op}{_mml_slot(node, 'm:sub')}{_mml_slot(node, 'm:sup')}</{tag}>"
        elif not sub_h:
            tag = "munder" if lim == "undOvr" else "msub"
            scripted = f"<{tag}>{op}{_mml_slot(node, 'm:sub')}</{tag}>"
        elif not sup_h:
            tag = "mover" if lim == "undOvr" else "msup"
            scripted = f"<{tag}>{op}{_mml_slot(node, 'm:sup')}</{tag}>"
        else:
            scripted = op
        return f"<mrow>{scripted}{_mml_slot(node, 'm:e')}</mrow>"
    if n == "m:func":
        return f"<mrow>{_mml_slot(node, 'm:fName')}<mo>\u2061</mo>{_mml_slot(node, 'm:e')}</mrow>"
    if n == "m:limLow":
        return f"<munder>{_mml_slot(node, 'm:e')}{_mml_slot(node, 'm:lim')}</munder>"
    if n == "m:limUpp":
        return f"<mover>{_mml_slot(node, 'm:e')}{_mml_slot(node, 'm:lim')}</mover>"
    if n == "m:acc":
        chr_ = _prop_val(node, "m:accPr", "m:chr") or "\u0302"
        return f'<mover accent="true">{_mml_slot(node, "m:e")}{_mo(chr_)}</mover>'
    if n == "m:bar":
        pos = _prop_val(node, "m:barPr", "m:pos") or "bot"
        line, tag = ("\u00af", "mover") if pos == "top" else ("\u005f", "munder")
        stretchy = ' stretchy="true"'
        return f"<{tag}>{_mml_slot(node, 'm:e')}{_mo(line, stretchy)}</{tag}>"
    if n == "m:m":
        rows = []
        for row in [c for c in children_of(node) if name_of(c) == "m:mr"]:
            cells = "".join(
                f"<mtd><mrow>{_mml_children(_content_children(cell))}</mrow></mtd>"
                for cell in [c for c in children_of(row) if name_of(c) == "m:e"]
            )
            rows.append(f"<mtr>{cells}</mtr>")
        return f"<mtable>{''.join(rows)}</mtable>"
    if n in ("m:box", "m:borderBox", "m:phant"):
        return _mml_slot(node, "m:e")
    if n == "m:t":
        return _run_text_to_mml(text_of(node), False)
    if not n:
        return ""
    return _mml_children(_content_children(node))


def omml_to_mathml(omml_xml: str) -> str:
    frags = omml_fragments_of(omml_xml)
    sources = frags if frags else [omml_xml]
    parts = []
    for src in sources:
        parsed = None
        try:
            parsed = xml_parse(src)
        except Exception:
            # A valid OMML *fragment* may consist of several adjacent root
            # elements (e.g. x+y is three m:r nodes).  Parse it inside a
            # synthetic oMath container instead of silently dropping it.
            pass
        root = next((n for n in parsed or [] if name_of(n) == "m:oMath"), None)
        if not root and "<m:oMath" not in src:
            try:
                parsed = xml_parse(f"<m:oMath>{src}</m:oMath>")
                root = next((n for n in parsed if name_of(n) == "m:oMath"), None)
            except Exception:
                root = None
        if not root:
            continue
        body = _mml_children(children_of(root))
        if body:
            parts.append(
                f'<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mrow>{body}</mrow></math>'
            )
    return "".join(parts)


# ---------- LaTeX -> OMML ----------
LATEX_SYMBOLS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "ϕ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "infty": "∞",
    "pm": "±",
    "mp": "∓",
    "times": "×",
    "div": "÷",
    "cdot": "⋅",
    "ast": "*",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "ne": "≠",
    "neq": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "∼",
    "propto": "∝",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "leftrightarrow": "↔",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "Leftrightarrow": "⇔",
    "partial": "∂",
    "nabla": "∇",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "supset": "⊃",
    "subseteq": "⊆",
    "supseteq": "⊇",
    "cup": "∪",
    "cap": "∩",
    "forall": "∀",
    "exists": "∃",
    "wedge": "∧",
    "vee": "∨",
    "neg": "¬",
    "angle": "∠",
    "perp": "⊥",
    "parallel": "∥",
    "ldots": "…",
    "cdots": "⋯",
    "vdots": "⋮",
    "ddots": "⋱",
    "prime": "′",
    "circ": "∘",
    "degree": "°",
    "bullet": "∙",
    "star": "⋆",
    "emptyset": "∅",
    "hbar": "ℏ",
    "ell": "ℓ",
    "Re": "ℜ",
    "Im": "ℑ",
    "aleph": "ℵ",
    "therefore": "∴",
    "because": "∵",
    "iff": "⇔",
    "implies": "⇒",
    "impliedby": "⇐",
    "mapsto": "↦",
    "longmapsto": "⟼",
    "setminus": "∖",
    "backslash": "∖",
    "triangle": "△",
    "triangledown": "▽",
    "triangleleft": "◁",
    "triangleright": "▷",
    "oplus": "⊕",
    "ominus": "⊖",
    "otimes": "⊗",
    "oslash": "⊘",
    "odot": "⊙",
    "bigcirc": "○",
    "diamond": "⋄",
    "square": "□",
    "clubsuit": "♣",
    "diamondsuit": "♢",
    "heartsuit": "♡",
    "spadesuit": "♠",
    "land": "∧",
    "lor": "∨",
    "lnot": "¬",
    "top": "⊤",
    "bot": "⊥",
    "uparrow": "↑",
    "downarrow": "↓",
    "updownarrow": "↕",
    "Uparrow": "⇑",
    "Downarrow": "⇓",
    "Updownarrow": "⇕",
}
MATHBB_UPPERCASE = {
    "A": "𝔸",
    "B": "𝔹",
    "C": "ℂ",
    "D": "𝔻",
    "E": "𝔼",
    "F": "𝔽",
    "G": "𝔾",
    "H": "ℍ",
    "I": "𝕀",
    "J": "𝕁",
    "K": "𝕂",
    "L": "𝕃",
    "M": "𝕄",
    "N": "ℕ",
    "O": "𝕆",
    "P": "ℙ",
    "Q": "ℚ",
    "R": "ℝ",
    "S": "𝕊",
    "T": "𝕋",
    "U": "𝕌",
    "V": "𝕍",
    "W": "𝕎",
    "X": "𝕏",
    "Y": "𝕐",
    "Z": "ℤ",
}


MATH_STYLE_OFFSETS = {
    "mathbf": (0x1D400, 0x1D41A, 0x1D7CE),
    "mathit": (0x1D434, 0x1D44E, None),
    "mathsf": (0x1D5A0, 0x1D5BA, 0x1D7E2),
    "mathtt": (0x1D670, 0x1D68A, 0x1D7F6),
}
MATH_STYLE_SPECIALS = {
    "mathcal": {
        "B": "ℬ",
        "E": "ℰ",
        "F": "ℱ",
        "H": "ℋ",
        "I": "ℐ",
        "L": "ℒ",
        "M": "ℳ",
        "R": "ℛ",
        "e": "ℯ",
        "g": "ℊ",
        "o": "ℴ",
    },
    "mathfrak": {"C": "ℭ", "H": "ℌ", "I": "ℑ", "R": "ℜ", "Z": "ℨ"},
}
MATH_STYLE_NAMES = {"mathcal": "MATHEMATICAL SCRIPT", "mathfrak": "MATHEMATICAL FRAKTUR"}


def _styled_math_text(style: str, value: str) -> str:
    """Return Unicode mathematical-alphabet text for a style command argument."""
    result: list[str] = []
    for character in value:
        if character.isspace():
            result.append(character)
            continue
        if style == "mathbb":
            if "A" <= character <= "Z":
                result.append(MATHBB_UPPERCASE[character])
            elif "a" <= character <= "z":
                result.append(chr(0x1D552 + ord(character) - ord("a")))
            elif "0" <= character <= "9":
                result.append(chr(0x1D7D8 + ord(character) - ord("0")))
            else:
                raise LatexError("\\mathbb supports only Latin letters and digits")
            continue
        if style in MATH_STYLE_OFFSETS:
            upper, lower, digits = MATH_STYLE_OFFSETS[style]
            if "A" <= character <= "Z":
                result.append(chr(upper + ord(character) - ord("A")))
            elif "a" <= character <= "z":
                result.append(chr(lower + ord(character) - ord("a")))
            elif digits is not None and "0" <= character <= "9":
                result.append(chr(digits + ord(character) - ord("0")))
            else:
                raise LatexError(
                    f"\\{style} supports only Latin letters" + (" and digits" if digits else "")
                )
            continue
        if style in MATH_STYLE_NAMES and character.isascii() and character.isalpha():
            special = MATH_STYLE_SPECIALS[style].get(character)
            if special:
                result.append(special)
                continue
            case = "CAPITAL" if character.isupper() else "SMALL"
            result.append(
                unicodedata.lookup(f"{MATH_STYLE_NAMES[style]} {case} {character.upper()}")
            )
            continue
        raise LatexError(f"\\{style} supports only Latin letters")
    return "".join(result)


def _apply_omml_style(omml: str, style: str) -> str:
    """Apply an Office Math run style while preserving the parsed structure."""
    return omml.replace("<m:r>", f'<m:r><m:rPr><m:sty m:val="{style}"/></m:rPr>', 1)


LATEX_FUNCTIONS = {
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "sinh",
    "cosh",
    "tanh",
    "coth",
    "arcsin",
    "arccos",
    "arctan",
    "ln",
    "log",
    "exp",
    "max",
    "min",
    "sup",
    "inf",
    "arg",
    "det",
    "gcd",
    "deg",
    "dim",
    "ker",
    "mod",
}
NARY_OPS = {
    "sum": ("∑", "undOvr"),
    "prod": ("∏", "undOvr"),
    "coprod": ("∐", "undOvr"),
    "bigcup": ("⋃", "undOvr"),
    "bigcap": ("⋂", "undOvr"),
    "int": ("∫", "subSup"),
    "iint": ("∬", "subSup"),
    "iiint": ("∭", "subSup"),
    "oint": ("∮", "subSup"),
    "iiiint": ("⨌", "subSup"),
    "idotsint": ("∫⋯∫", "subSup"),
    "bigsqcup": ("⨆", "undOvr"),
    "bigvee": ("⋁", "undOvr"),
    "bigwedge": ("⋀", "undOvr"),
    "bigodot": ("⨀", "undOvr"),
    "bigotimes": ("⨂", "undOvr"),
    "bigoplus": ("⨁", "undOvr"),
}
ACCENT_CHARS = {
    "hat": "\u0302",
    "widehat": "\u0302",
    "bar": "\u0304",
    "vec": "\u20d7",
    "overrightarrow": "\u20d7",
    "overleftarrow": "\u20d6",
    "dot": "\u0307",
    "ddot": "\u0308",
    "tilde": "\u0303",
    "widetilde": "\u0303",
    "check": "\u030c",
    "breve": "\u0306",
}
MATRIX_DELIMS = {
    "matrix": None,
    "pmatrix": ("(", ")"),
    "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"),
    "vmatrix": ("|", "|"),
    "Vmatrix": ("‖", "‖"),
    "cases": ("{", ""),
    "array": None,
    "aligned": None,
    "align": None,
    "align*": None,
    "gather": None,
    "gather*": None,
    "gathered": None,
    "split": None,
    "smallmatrix": None,
}
LEFT_RIGHT_CHARS = {
    "(": "(",
    ")": ")",
    "[": "[",
    "]": "]",
    "|": "|",
    ".": "",
    "{": "{",
    "}": "}",
    "Vert": "‖",
    "lVert": "‖",
    "rVert": "‖",
    "langle": "⟨",
    "rangle": "⟩",
    "lfloor": "⌊",
    "rfloor": "⌋",
    "lceil": "⌈",
    "rceil": "⌉",
}


class LatexError(Exception):
    pass


class _P:
    def __init__(self, src):
        self.src, self.pos = src, 0


def _peek(p):
    return p.src[p.pos] if p.pos < len(p.src) else ""


def _skip(p):
    while p.pos < len(p.src) and p.src[p.pos].isspace():
        p.pos += 1


def _read_name(p):
    m = re.match(r"[A-Za-z]+", p.src[p.pos :])
    if m:
        p.pos += len(m.group(0))
        return m.group(0)
    ch = p.src[p.pos] if p.pos < len(p.src) else ""
    p.pos += 1
    return ch


def _math_run(text, plain=False):
    if text == "":
        return ""
    rpr = '<m:rPr><m:sty m:val="p"/></m:rPr>' if plain else ""
    return f'<m:r>{rpr}<m:t xml:space="preserve">{escape_xml_text(text)}</m:t></m:r>'


def _parse_group(p):
    _skip(p)
    if _peek(p) == "{":
        p.pos += 1
        out = _parse_sequence(p, lambda: _peek(p) == "}")
        if _peek(p) != "}":
            raise LatexError("Missing matching }")
        p.pos += 1
        return out
    if _peek(p) == "\\":
        p.pos += 1
        return _parse_control(p)
    ch = _peek(p)
    if ch == "" or ch in "{}^_&":
        raise LatexError("An argument is required here")
    p.pos += 1
    return _math_run(ch)


def _read_brace_text(p):
    _skip(p)
    if _peek(p) != "{":
        raise LatexError("Expected { here")
    p.pos += 1
    depth, out = 1, []
    while p.pos < len(p.src):
        ch = p.src[p.pos]
        p.pos += 1
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(out)
        if depth > 0:
            out.append(ch)
    raise LatexError("Missing matching }")


def _parse_sequence(p, stop):
    atoms = []
    while True:
        _skip(p)
        if p.pos >= len(p.src) or stop():
            break
        ch = _peek(p)
        if ch in "^_":
            p.pos += 1
            script = _parse_group(p)
            other = _peek(p)
            base = atoms.pop() if atoms else _math_run("")
            if other and other in "^_" and other != ch:
                p.pos += 1
                second = _parse_group(p)
                sub = script if ch == "_" else second
                sup = script if ch == "^" else second
                atoms.append(
                    f"<m:sSubSup><m:e>{base}</m:e><m:sub>{sub}</m:sub><m:sup>{sup}</m:sup></m:sSubSup>"
                )
            elif ch == "^":
                atoms.append(f"<m:sSup><m:e>{base}</m:e><m:sup>{script}</m:sup></m:sSup>")
            else:
                atoms.append(f"<m:sSub><m:e>{base}</m:e><m:sub>{script}</m:sub></m:sSub>")
            continue
        atoms.append(_parse_atom(p))
    return "".join(atoms)


def _parse_atom(p):
    _skip(p)
    ch = _peek(p)
    if ch == "":
        return ""
    if ch == "{":
        return _parse_group(p)
    if ch == "}":
        raise LatexError("Unexpected }")
    if ch == "\\":
        p.pos += 1
        return _parse_control(p)
    text = ""
    while p.pos < len(p.src) and _peek(p) not in "\\{}^_&\n":
        text += p.src[p.pos]
        p.pos += 1
    if text == "":
        raise LatexError(f'Cannot parse: "{ch}"')
    chars = list(text)
    if _peek(p) in "^_" and len(chars) > 1:
        p.pos -= len(chars[-1])
        return _math_run("".join(chars[:-1]))
    return _math_run(text)


def _nary_omml(p, chr_, lim_loc):
    sub = sup = ""
    for _ in range(2):
        _skip(p)
        ch = _peek(p)
        if ch == "_" and sub == "":
            p.pos += 1
            sub = _parse_group(p)
        elif ch == "^" and sup == "":
            p.pos += 1
            sup = _parse_group(p)
        else:
            break
    _skip(p)
    operand = _parse_group(p) if _peek(p) == "{" else ""
    pr = (
        f'<m:naryPr><m:chr m:val="{escape_xml_attr(chr_)}"/><m:limLoc m:val="{lim_loc}"/>'
        + ('<m:subHide m:val="1"/>' if sub == "" else "")
        + ('<m:supHide m:val="1"/>' if sup == "" else "")
        + "</m:naryPr>"
    )
    return (
        f"<m:nary>{pr}"
        + (f"<m:sub>{sub}</m:sub>" if sub else "")
        + (f"<m:sup>{sup}</m:sup>" if sup else "")
        + f"<m:e>{operand}</m:e></m:nary>"
    )


def _matrix_omml(p, env):
    delims = MATRIX_DELIMS[env]
    if env == "array":
        _read_brace_text(p)
    rows, done = [[]], False
    while not done:
        cell = _parse_sequence(
            p,
            lambda: (
                _peek(p) == "&"
                or p.src.startswith("\\\\", p.pos)
                or p.src.startswith("\\end", p.pos)
            ),
        )
        rows[-1].append(cell)
        if _peek(p) == "&":
            p.pos += 1
        elif p.src.startswith("\\\\", p.pos):
            p.pos += 2
            rows.append([])
        elif p.src.startswith("\\end", p.pos):
            p.pos += 4
            closing = _read_brace_text(p)
            if closing != env:
                raise LatexError(f"\\end{{{closing}}} does not match \\begin{{{env}}}")
            done = True
        else:
            raise LatexError(f"\\begin{{{env}}} is missing \\end{{{env}}}")
    body = "".join(
        "<m:mr>" + "".join(f"<m:e>{c}</m:e>" for c in row) + "</m:mr>"
        for row in rows
        if len(row) > 1 or row[0] != ""
    )
    matrix = f"<m:m>{body}</m:m>"
    if not delims:
        return matrix
    return (
        "<m:d><m:dPr>" + f'<m:begChr m:val="{escape_xml_attr(delims[0])}"/>'
        f'<m:endChr m:val="{escape_xml_attr(delims[1])}"/>' + f"</m:dPr><m:e>{matrix}</m:e></m:d>"
    )


def _substack_omml(p) -> str:
    content = _read_brace_text(p)
    nested = _P(content)
    rows: list[str] = []
    while nested.pos < len(nested.src):
        row = _parse_sequence(nested, lambda: nested.src.startswith("\\\\", nested.pos))
        rows.append(row)
        if nested.src.startswith("\\\\", nested.pos):
            nested.pos += 2
        else:
            break
    body = "".join(f"<m:mr><m:e>{row}</m:e></m:mr>" for row in rows)
    return f"<m:m>{body}</m:m>"


def _read_delimiter(p):
    _skip(p)
    if _peek(p) == "\\":
        start = p.pos
        p.pos += 1
        name = _read_name(p)
        if name in LEFT_RIGHT_CHARS:
            return LEFT_RIGHT_CHARS[name]
        p.pos = start
        raise LatexError(f"Unsupported delimiter: \\{name}")
    ch = _peek(p)
    if ch in LEFT_RIGHT_CHARS:
        p.pos += 1
        return LEFT_RIGHT_CHARS[ch]
    raise LatexError(f'Unsupported delimiter: "{ch}"')


def _accent_omml(base: str, accent: str) -> str:
    return (
        f'<m:acc><m:accPr><m:chr m:val="{escape_xml_attr(accent)}"/></m:accPr>'
        f"<m:e>{base}</m:e></m:acc>"
    )


def _not_omml(base: str) -> str:
    replacements = {
        "=": "≠",
        "∈": "∉",
        "⊂": "⊄",
        "⊃": "⊅",
        "⊆": "⊈",
        "⊇": "⊉",
        "≤": "≰",
        "≥": "≱",
        "≈": "≉",
        "≡": "≢",
        "∼": "≁",
    }
    token_match = re.fullmatch(r'<m:r><m:t xml:space="preserve">(.+)</m:t></m:r>', base)
    if token_match and token_match.group(1) in replacements:
        return _math_run(replacements[token_match.group(1)])
    return _accent_omml(base, "̸")


def _limits_omml(label: str, p) -> str:
    _skip(p)
    if _peek(p) == "_":
        p.pos += 1
        lower = _parse_group(p)
        return f"<m:limLow><m:e>{_math_run(label, True)}</m:e><m:lim>{lower}</m:lim></m:limLow>"
    return _math_run(label, True)


def _parse_control(p):
    name = _read_name(p)
    if name in LATEX_SYMBOLS:
        return _math_run(LATEX_SYMBOLS[name])
    if name in NARY_OPS:
        return _nary_omml(p, *NARY_OPS[name])
    if name in ACCENT_CHARS:
        return _accent_omml(_parse_group(p), ACCENT_CHARS[name])
    if name in {"overset", "stackrel"}:
        annotation, base = _parse_group(p), _parse_group(p)
        return f"<m:limUpp><m:e>{base}</m:e><m:lim>{annotation}</m:lim></m:limUpp>"
    if name == "underset":
        annotation, base = _parse_group(p), _parse_group(p)
        return f"<m:limLow><m:e>{base}</m:e><m:lim>{annotation}</m:lim></m:limLow>"
    if name == "not":
        return _not_omml(_parse_group(p))
    if name in LATEX_FUNCTIONS:
        return _math_run(name, True)
    if name in ("frac", "dfrac", "tfrac"):
        return f"<m:f><m:num>{_parse_group(p)}</m:num><m:den>{_parse_group(p)}</m:den></m:f>"
    if name == "binom":
        top, bot = _parse_group(p), _parse_group(p)
        return (
            '<m:d><m:e><m:f><m:fPr><m:type m:val="noBar"/></m:fPr>'
            f"<m:num>{top}</m:num><m:den>{bot}</m:den></m:f></m:e></m:d>"
        )
    if name == "sqrt":
        _skip(p)
        deg = ""
        if _peek(p) == "[":
            p.pos += 1
            close = p.src.find("]", p.pos)
            if close == -1:
                raise LatexError("Missing matching ]")
            sub = _P(p.src[p.pos : close])
            deg = _parse_sequence(sub, lambda: sub.pos >= len(sub.src))
            p.pos = close + 1
        inner = _parse_group(p)
        if deg == "":
            return f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>{inner}</m:e></m:rad>'
        return f"<m:rad><m:deg>{deg}</m:deg><m:e>{inner}</m:e></m:rad>"
    if name == "overline":
        return f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>{_parse_group(p)}</m:e></m:bar>'
    if name == "underline":
        return f'<m:bar><m:barPr><m:pos m:val="bot"/></m:barPr><m:e>{_parse_group(p)}</m:e></m:bar>'
    if name == "underbrace":
        return (
            '<m:groupChr><m:groupChrPr><m:chr m:val="⏟"/><m:pos m:val="bot"/></m:groupChrPr>'
            f"<m:e>{_parse_group(p)}</m:e></m:groupChr>"
        )
    if name == "overbrace":
        return (
            '<m:groupChr><m:groupChrPr><m:chr m:val="⏞"/><m:pos m:val="top"/></m:groupChrPr>'
            f"<m:e>{_parse_group(p)}</m:e></m:groupChr>"
        )
    if name in {"mathbb", "mathcal", "mathfrak", "mathsf", "mathtt"}:
        return _math_run(_styled_math_text(name, _read_brace_text(p)))
    if name in {"mathbf", "boldsymbol"}:
        return _apply_omml_style(_parse_group(p), "b")
    if name == "mathit":
        return _apply_omml_style(_parse_group(p), "i")
    if name == "mathnormal":
        return _parse_group(p)
    if name in ("text", "mathrm", "operatorname"):
        return _math_run(_read_brace_text(p), True)
    if name == "lim":
        return _limits_omml("lim", p)
    if name in {"limsup", "liminf", "argmax", "argmin"}:
        return _limits_omml(name, p)
    if name == "left":
        beg = _read_delimiter(p)
        body = _parse_sequence(p, lambda: p.src.startswith("\\right", p.pos))
        if not p.src.startswith("\\right", p.pos):
            raise LatexError("\\left is missing a matching \\right")
        p.pos += len("\\right")
        end = _read_delimiter(p)
        return (
            "<m:d><m:dPr>" + f'<m:begChr m:val="{escape_xml_attr(beg)}"/>'
            f'<m:endChr m:val="{escape_xml_attr(end)}"/>' + f"</m:dPr><m:e>{body}</m:e></m:d>"
        )
    if name == "substack":
        return _substack_omml(p)
    if name == "begin":
        env = _read_brace_text(p)
        if env not in MATRIX_DELIMS:
            raise LatexError(f"Unsupported environment: \\begin{{{env}}}")
        return _matrix_omml(p, env)
    if name in (
        ",",
        ";",
        ":",
        " ",
        "!",
        "quad",
        "qquad",
        "enspace",
        "thinspace",
        "medspace",
        "thickspace",
    ):
        return _math_run(" ")
    if name in {"displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle"}:
        return ""
    if name == "\\":
        raise LatexError("\\\\ is only allowed inside matrix environments")
    if name in "{}%&$#_^":
        return _math_run(name)
    raise LatexError(f"Unsupported command: \\{name}")


def latex_to_omml(latex: str) -> str:
    p = _P(latex)
    out = _parse_sequence(p, lambda: p.pos >= len(p.src))
    if p.pos < len(p.src):
        raise LatexError(f'Cannot parse: "{p.src[p.pos : p.pos + 12]}"')
    return out


def math_paragraph_xml(omml: str, align="center") -> str:
    val = str(align or "center")
    jc = "" if val == "center" else f'<w:pPr><w:jc w:val="{escape_xml_attr(val)}"/></w:pPr>'
    return (
        f'<w:p>{jc}<m:oMathPara><m:oMathParaPr><m:jc m:val="{escape_xml_attr(val)}"/></m:oMathParaPr>'
        f"<m:oMath>{omml}</m:oMath></m:oMathPara></w:p>"
    )
