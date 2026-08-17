"""Fuzzy find/replace for patch_file with conservative drift recovery."""

from __future__ import annotations

from difflib import SequenceMatcher

_TAB_WIDTH = 4


def _normalize_line(line: str) -> str:
    """Normalize tabs and insignificant repeated whitespace."""
    return " ".join(line.expandtabs(_TAB_WIDTH).split())


def _detect_eol(lines: list[str]) -> str:
    for ln in lines:
        if ln.endswith("\r\n"):
            return "\r\n"
        if ln.endswith("\n"):
            return "\n"
        if ln.endswith("\r"):
            return "\r"
    return "\n"


def _apply_window(
    text_lines: list[str],
    find_lines: list[str],
    replace: str,
    start: int,
    count: int,
    *,
    flatten_indent: bool = False,
) -> str | None:
    """Replace one located window while preserving file EOL and relative indentation."""
    orig_indent = len(text_lines[start]) - len(text_lines[start].lstrip())
    replace_lines = replace.splitlines()
    if replace_lines:
        if flatten_indent:
            pad = " " * orig_indent
            replace_lines = [pad + ln.lstrip() if ln.strip() else ln for ln in replace_lines]
        else:
            find_indent = len(find_lines[0]) - len(find_lines[0].lstrip())
            indent_diff = orig_indent - find_indent
            if indent_diff > 0:
                pad = " " * indent_diff
                replace_lines = [pad + ln if ln.strip() else ln for ln in replace_lines]
            elif indent_diff < 0:
                cut = -indent_diff
                if any(
                    ln.strip() and (ln[:cut].strip() != "" or "\t" in ln[:cut])
                    for ln in replace_lines
                ):
                    return None
                replace_lines = [ln[cut:] if ln.strip() else ln for ln in replace_lines]

    window = text_lines[start : start + count]
    eol = _detect_eol(window)
    new_replace = eol.join(replace_lines)
    if window:
        last_line = window[-1]
        if last_line.endswith(("\n", "\r")) and new_replace and not new_replace.endswith(("\n", "\r")):
            new_replace += eol
    return "".join([*text_lines[:start], new_replace, *text_lines[start + count :]])


def _approximate_window(
    text_lines: list[str],
    find_lines: list[str],
) -> tuple[int, int] | None:
    """Find one *unambiguous* near-match.

    This recovers small stale-context drift (renamed literal, punctuation, etc.)
    without turning patch_file into an unsafe "replace something similar" tool.
    """
    count = len(find_lines)
    if not count:
        return None

    target_lines = [_normalize_line(line) for line in find_lines]
    target = "\n".join(target_lines)
    # Tiny one-liners are too easy to match accidentally.
    if count == 1 and len(target) < 16:
        return None

    scored: list[tuple[float, int]] = []
    for start in range(len(text_lines) - count + 1):
        candidate = "\n".join(
            _normalize_line(line.rstrip("\r\n"))
            for line in text_lines[start : start + count]
        )
        ratio = SequenceMatcher(None, target, candidate, autojunk=False).ratio()
        scored.append((ratio, start))

    if not scored:
        return None
    scored.sort(reverse=True)
    best_ratio, best_start = scored[0]
    threshold = 0.90 if count == 1 else 0.86
    if best_ratio < threshold:
        return None

    # Require a meaningful gap to the runner-up. If two regions look equally
    # plausible, failing loudly is safer than modifying the wrong one.
    if len(scored) > 1:
        second_ratio = scored[1][0]
        if second_ratio >= threshold and best_ratio - second_ratio < 0.035:
            return None
    return best_start, count


def _fuzzy_find_replace(text: str, find: str, replace: str) -> tuple[str, bool]:
    """Find/replace with increasingly tolerant, still conservative strategies.

    Order:
    1. normalized whitespace, preserving relative indentation;
    2. exact stripped lines as a compatibility fallback;
    3. one unique high-confidence near-match for slightly stale context.
    """
    find_lines = find.splitlines()
    text_lines = text.splitlines(keepends=True)
    if not find_lines:
        return text, False

    norm_find = [_normalize_line(ln) for ln in find_lines]
    norm_text = [_normalize_line(ln.rstrip("\r\n")) for ln in text_lines]

    for i in range(len(text_lines) - len(find_lines) + 1):
        if norm_text[i : i + len(find_lines)] == norm_find:
            result = _apply_window(text_lines, find_lines, replace, i, len(find_lines))
            return (result, True) if result is not None else (text, False)

    strip_find = [ln.strip() for ln in find_lines if ln.strip()]
    if strip_find:
        for i in range(len(text_lines) - len(strip_find) + 1):
            window = [ln.rstrip("\r\n").strip() for ln in text_lines[i : i + len(strip_find)]]
            if window == strip_find:
                result = _apply_window(
                    text_lines, find_lines, replace, i, len(strip_find), flatten_indent=True
                )
                return (result, True) if result is not None else (text, False)

    approximate = _approximate_window(text_lines, find_lines)
    if approximate is not None:
        start, count = approximate
        result = _apply_window(text_lines, find_lines, replace, start, count)
        return (result, True) if result is not None else (text, False)

    return text, False
