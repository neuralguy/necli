"""Robust JSON parsing for LLM output.

Handles: invalid escapes, raw newlines, trailing commas,
single quotes, unquoted keys, greedy content extraction.
"""

import json
import re
from typing import Optional


def robust_json_loads(text: str) -> dict | list | None:
    if not text or not text.strip():
        return None

    text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    cleaned = text.lstrip("\ufeff\u200b\u200c\u200d")
    if cleaned != text:
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass
    else:
        cleaned = text

    fixed_newlines = _fix_raw_newlines_in_strings(cleaned)
    if fixed_newlines != cleaned:
        try:
            return json.loads(fixed_newlines)
        except (json.JSONDecodeError, ValueError):
            pass

    fixed_esc = _fix_invalid_escapes(fixed_newlines)
    if fixed_esc != fixed_newlines:
        try:
            return json.loads(fixed_esc)
        except (json.JSONDecodeError, ValueError):
            pass

    no_trailing = re.sub(r",\s*([}\]])", r"\1", fixed_esc)
    if no_trailing != fixed_esc:
        try:
            return json.loads(no_trailing)
        except (json.JSONDecodeError, ValueError):
            pass

    if '"' not in no_trailing and "'" in no_trailing:
        single_fixed = _swap_single_quote_delimiters(no_trailing)
        if single_fixed is not None:
            try:
                return json.loads(single_fixed)
            except (json.JSONDecodeError, ValueError):
                pass

    no_comments = re.sub(r"//[^\n]*", "", no_trailing)
    no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL)
    if no_comments.strip() != no_trailing.strip():
        try:
            return json.loads(no_comments)
        except (json.JSONDecodeError, ValueError):
            pass

    unquoted_keys = re.sub(r"(?<=[{,])\s*(\w+)\s*:", r' "\1":', no_trailing)
    if unquoted_keys != no_trailing:
        try:
            return json.loads(unquoted_keys)
        except (json.JSONDecodeError, ValueError):
            pass

    combo = re.sub(r",\s*([}\]])", r"\1", unquoted_keys)
    combo = _fix_raw_newlines_in_strings(combo)
    combo = _fix_invalid_escapes(combo)
    # Пропускаем лишний parse, если combo не отличается от уже опробованных
    # кандидатов (unquoted_keys прошёл выше, no_trailing — ещё раньше).
    if combo != unquoted_keys and combo != no_trailing:
        try:
            return json.loads(combo)
        except (json.JSONDecodeError, ValueError):
            pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        extracted = text[brace_start : brace_end + 1]
        try:
            return json.loads(extracted)
        except (json.JSONDecodeError, ValueError):
            pass
        ext_fixed = _fix_raw_newlines_in_strings(extracted)
        ext_fixed = _fix_invalid_escapes(ext_fixed)
        ext_fixed = re.sub(r",\s*([}\]])", r"\1", ext_fixed)
        try:
            return json.loads(ext_fixed)
        except (json.JSONDecodeError, ValueError):
            pass

    greedy_result = greedy_extract_content_json(text)
    if greedy_result is not None:
        return greedy_result

    return None


def _swap_single_quote_delimiters(s: str) -> Optional[str]:
    """Swap only single-quotes that act as JSON string delimiters to double-quotes.

    A single-quote is treated as a delimiter when it opens/closes a string at a
    structural position (after { [ , : or whitespace, or before } ] , : or
    whitespace/end). Apostrophes inside string values are preserved. Returns None
    if the structure looks ambiguous so the caller can skip this heuristic.
    """
    out = []
    i = 0
    n = len(s)
    in_string = False
    structural_before = "{[,:"

    while i < n:
        ch = s[i]
        if not in_string:
            if ch == "'":
                j = i - 1
                while j >= 0 and s[j].isspace():
                    j -= 1
                if j < 0 or s[j] in structural_before:
                    in_string = True
                    out.append('"')
                    i += 1
                    continue
                return None
            out.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            out.append(ch)
            out.append(s[i + 1])
            i += 2
            continue

        if ch == "'":
            k = i + 1
            while k < n and s[k].isspace():
                k += 1
            if k >= n or s[k] in "}],:":
                in_string = False
                out.append('"')
                i += 1
                continue
            out.append("'")
            i += 1
            continue

        out.append(ch)
        i += 1

    if in_string:
        return None
    return "".join(out)

def greedy_extract_content_json(text: str) -> Optional[dict]:
    text = text.strip()

    content_start_re = re.compile(r'"content"\s*:\s*"')
    m = content_start_re.search(text)
    if not m:
        return None

    value_start = m.end()

    last_brace = text.rfind("}")
    if last_brace < value_start:
        return None

    search_area = text[value_start:last_brace]
    close_pos = _find_content_close_quote(search_area)

    if close_pos is None:
        return None
    raw_content = search_area[:close_pos]

    decoded_content = decode_json_string_value(raw_content)

    other_fields = {}

    path_val = extract_field_value(text, "path")
    if path_val:
        other_fields["path"] = path_val

    enc_val = extract_field_value(text, "encoding")
    if enc_val:
        other_fields["encoding"] = enc_val

    b64_val = extract_field_value(text, "b64")
    if b64_val:
        other_fields["b64"] = b64_val

    other_fields["content"] = decoded_content

    if "path" not in other_fields:
        return None

    return other_fields


def _find_content_close_quote(text: str) -> Optional[int]:
    candidates = []
    i = len(text) - 1
    while i >= 0:
        if text[i] == '"':
            num_backslashes = 0
            j = i - 1
            while j >= 0 and text[j] == "\\":
                num_backslashes += 1
                j -= 1
            if num_backslashes % 2 == 0:
                candidates.append(i)
        i -= 1

    for pos in candidates:
        after = text[pos + 1 :].strip()
        if not after:
            return pos
        if after.startswith(","):
            return pos
        if after.startswith("}"):
            return pos

    return None


def _fix_raw_newlines_in_strings(text: str) -> str:
    result = []
    i = 0
    in_string = False

    while i < len(text):
        ch = text[i]

        if not in_string:
            if ch == '"':
                in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < len(text):
            next_ch = text[i + 1]
            if next_ch in ('"', "\\", "/", "b", "f", "n", "r", "t"):
                result.append(ch)
                result.append(next_ch)
                i += 2
                continue
            if next_ch == "u" and i + 5 < len(text):
                hex_part = text[i + 2 : i + 6]
                if all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    result.append(text[i : i + 6])
                    i += 6
                    continue
            result.append(ch)
            result.append(next_ch)
            i += 2
            continue

        if ch == '"':
            in_string = False
            result.append(ch)
            i += 1
            continue

        if ch == "\n":
            result.append("\\n")
            i += 1
            continue
        if ch == "\r":
            if i + 1 < len(text) and text[i + 1] == "\n":
                result.append("\\n")
                i += 2
            else:
                result.append("\\r")
                i += 1
            continue
        if ch == "\t":
            result.append("\\t")
            i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _fix_invalid_escapes(text: str) -> str:
    result = []
    i = 0
    in_string = False

    while i < len(text):
        ch = text[i]

        if not in_string:
            if ch == '"':
                in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < len(text):
            next_ch = text[i + 1]
            if next_ch in ('"', "\\", "/", "b", "f", "n", "r", "t"):
                result.append(ch)
                result.append(next_ch)
                i += 2
                continue
            if next_ch == "u" and i + 5 < len(text):
                hex_part = text[i + 2 : i + 6]
                if all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    result.append(text[i : i + 6])
                    i += 6
                    continue
            result.append("\\\\")
            result.append(next_ch)
            i += 2
            continue

        if ch == '"':
            in_string = False

        result.append(ch)
        i += 1

    return "".join(result)


def extract_field_value(text: str, field: str) -> Optional[str]:
    patterns = [
        f'"{field}"\\s*:\\s*"',
        f"'{field}'\\s*:\\s*'",
        f'{field}\\s*:\\s*"',
    ]

    for pat_str in patterns:
        pat = re.compile(pat_str)
        m = pat.search(text)
        if not m:
            continue

        end_pos = m.end()
        quote_char = text[end_pos - 1] if end_pos > 0 else '"'

        if field == "content":
            return _extract_content_value_greedy(text, end_pos, quote_char)

        value_chars = []
        i = end_pos
        while i < len(text):
            ch = text[i]
            if ch == "\\" and i + 1 < len(text):
                next_ch = text[i + 1]
                if next_ch == "n":
                    value_chars.append("\n")
                elif next_ch == "t":
                    value_chars.append("\t")
                elif next_ch == "r":
                    value_chars.append("\r")
                elif next_ch == quote_char:
                    value_chars.append(quote_char)
                elif next_ch == "\\":
                    value_chars.append("\\")
                else:
                    value_chars.append(ch)
                    value_chars.append(next_ch)
                i += 2
                continue
            if ch == quote_char:
                return "".join(value_chars)
            if ch in ("\n", "\r"):
                return "".join(value_chars)
            value_chars.append(ch)
            i += 1

    return None


def _extract_content_value_greedy(
    text: str, value_start: int, quote_char: str
) -> Optional[str]:
    remaining = text[value_start:]

    best_end = None
    i = len(remaining) - 1
    while i >= 0:
        if remaining[i] == quote_char:
            num_bs = 0
            j = i - 1
            while j >= 0 and remaining[j] == "\\":
                num_bs += 1
                j -= 1
            if num_bs % 2 != 0:
                i -= 1
                continue
            after = remaining[i + 1 :].strip()
            if not after or after[0] in (",", "}", "]"):
                best_end = i
                break
        i -= 1

    if best_end is None:
        raw_value = remaining
    else:
        raw_value = remaining[:best_end]

    return decode_json_string_value(raw_value)


def decode_json_string_value(raw: str) -> str:
    result = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            next_ch = raw[i + 1]
            if next_ch == "n":
                result.append("\n")
                i += 2
                continue
            elif next_ch == "t":
                result.append("\t")
                i += 2
                continue
            elif next_ch == "r":
                result.append("\r")
                i += 2
                continue
            elif next_ch == '"':
                result.append('"')
                i += 2
                continue
            elif next_ch == "\\":
                result.append("\\")
                i += 2
                continue
            elif next_ch == "/":
                result.append("/")
                i += 2
                continue
            elif next_ch == "b":
                result.append("\b")
                i += 2
                continue
            elif next_ch == "f":
                result.append("\f")
                i += 2
                continue
            elif next_ch == "u" and i + 5 < len(raw):
                hex_part = raw[i + 2 : i + 6]
                if all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    result.append(chr(int(hex_part, 16)))
                    i += 6
                    continue
            result.append(ch)
            result.append(next_ch)
            i += 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)



