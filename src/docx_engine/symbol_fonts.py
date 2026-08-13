"""Декодирование Symbol/Wingdings."""

from __future__ import annotations

SYMBOL = {
    0x41: "Α",
    0x42: "Β",
    0x43: "Χ",
    0x44: "Δ",
    0x45: "Ε",
    0x46: "Φ",
    0x47: "Γ",
    0x48: "Η",
    0x49: "Ι",
    0x4A: "ϑ",
    0x4B: "Κ",
    0x4C: "Λ",
    0x4D: "Μ",
    0x4E: "Ν",
    0x4F: "Ο",
    0x50: "Π",
    0x51: "Θ",
    0x52: "Ρ",
    0x53: "Σ",
    0x54: "Τ",
    0x55: "Υ",
    0x56: "ς",
    0x57: "Ω",
    0x58: "Ξ",
    0x59: "Ψ",
    0x5A: "Ζ",
    0x61: "α",
    0x62: "β",
    0x63: "χ",
    0x64: "δ",
    0x65: "ε",
    0x66: "φ",
    0x67: "γ",
    0x68: "η",
    0x69: "ι",
    0x6A: "ϕ",
    0x6B: "κ",
    0x6C: "λ",
    0x6D: "μ",
    0x6E: "ν",
    0x6F: "ο",
    0x70: "π",
    0x71: "θ",
    0x72: "ρ",
    0x73: "σ",
    0x74: "τ",
    0x75: "υ",
    0x76: "ϖ",
    0x77: "ω",
    0x78: "ξ",
    0x79: "ψ",
    0x7A: "ζ",
    0x22: "∀",
    0x24: "∃",
    0xA3: "≤",
    0xA5: "∞",
    0xAB: "↔",
    0xAC: "←",
    0xAD: "↑",
    0xAE: "→",
    0xAF: "↓",
    0xB0: "°",
    0xB1: "±",
    0xB3: "≥",
    0xB4: "×",
    0xB6: "∂",
    0xB7: "•",
    0xB8: "÷",
    0xB9: "≠",
    0xBA: "≡",
    0xBB: "≈",
    0xBC: "…",
    0xC4: "⊗",
    0xC5: "⊕",
    0xC6: "∅",
    0xC7: "∩",
    0xC8: "∪",
    0xC9: "⊃",
    0xCC: "⊂",
    0xCE: "∈",
    0xD1: "∇",
    0xD5: "∏",
    0xD6: "√",
    0xD7: "⋅",
    0xD8: "¬",
    0xD9: "∧",
    0xDA: "∨",
    0xE5: "∑",
    0xF2: "∫",
}
WINGDINGS = {
    0x21: "✏",
    0x22: "✂",
    0x28: "☎",
    0x2A: "✉",
    0x45: "☜",
    0x46: "☞",
    0x47: "☝",
    0x48: "☟",
    0x4A: "☺",
    0x4B: "😐",
    0x4C: "☹",
    0x6C: "●",
    0x6D: "❍",
    0x6E: "■",
    0x6F: "❑",
    0x70: "❒",
    0x75: "◆",
    0x76: "❖",
    0x78: "⌧",
    0xA7: "▪",
    0xA8: "□",
    0xAB: "★",
    0xD8: "➢",
    0xFB: "✗",
    0xFC: "✓",
    0xFD: "☒",
    0xFE: "☑",
}
MAPS = {
    "symbol": SYMBOL,
    "wingdings": WINGDINGS,
    "wingdings 2": {},
    "wingdings 3": {},
    "webdings": {},
}


def is_symbol_font(font) -> bool:
    return bool(font) and font.strip().lower() in MAPS


def decode_symbol_char(font: str, code: int):
    m = MAPS.get(font.strip().lower())
    if not m:
        return None
    low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
    return m.get(low)


def decode_symbol_text(font: str, text: str):
    """Decode a run only when every symbol has a known Unicode mapping.

    Returning ``None`` means "preserve original text + symbol font"; callers
    must not treat it as an empty string. This makes unsupported Wingdings 2/3
    and Webdings lossless even though their full proprietary maps are not
    bundled here.
    """
    if not is_symbol_font(font):
        return None
    out = []
    for ch in text:
        code = ord(ch)
        if code <= 0x20 or code == 0xA0:
            out.append(ch)
            continue
        mapped = decode_symbol_char(font, code)
        if mapped is None:
            return None
        out.append(mapped)
    return "".join(out)
