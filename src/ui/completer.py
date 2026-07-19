"""Autocomplete files and folders after @, slash commands."""

import os
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion, merge_completers

from config.i18n import t as _
from ui._filters import should_ignore as _should_ignore
from ui.formatting import format_size as _format_size

_MAX_RESULTS = 50


def _scan_dir(base, prefix=""):
    results = []
    try:
        entries = sorted(
            os.scandir(base),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except (PermissionError, OSError):
        return results
    for entry in entries:
        if _should_ignore(entry.name, entry.is_dir()):
            continue
        rel = f"{prefix}{entry.name}" if prefix else entry.name
        if entry.is_dir():
            results.append((rel + "/", True))
        else:
            results.append((rel, False))
        if len(results) >= _MAX_RESULTS * 3:
            break
    return results


def _find_at_reference(text, cursor_pos):
    if cursor_pos <= 0 or cursor_pos > len(text):
        return None
    left = text[:cursor_pos]
    at_pos = -1
    for i in range(len(left) - 1, -1, -1):
        ch = left[i]
        if ch == "@":
            if i == 0 or left[i - 1] in (" ", "\t", "\n"):
                at_pos = i
                break
            else:
                return None
        if ch in (" ", "\t", "\n"):
            return None
    if at_pos < 0:
        return None
    return left[at_pos + 1:]




def _format_tokens(num_bytes):
    tokens = num_bytes // 4
    if tokens < 1000:
        return f"~{tokens} tok"
    if tokens < 1_000_000:
        return f"~{tokens / 1000:.1f}K tok"
    return f"~{tokens / 1_000_000:.1f}M tok"


def _dir_total_size(path):
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not _should_ignore(d, True)]
            for f in files:
                if _should_ignore(f, False):
                    continue
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except (PermissionError, OSError):
        pass
    return total


def _slash_commands():
    """Команды + метаданные для автокомплита.

    Возвращает [(name, desc_key, args_hint, toggle_config_key)], отсортировано
    по категории и canonical name. Берётся из commands/registry.
    """
    from commands.registry import CATEGORIES, COMMANDS
    cat_order = {cat: i for i, (cat, _) in enumerate(CATEGORIES)}
    items = []
    for c in COMMANDS:
        if not c.completable:
            continue
        items.append((c.name, c.desc_key, c.args_hint, c.toggle_config_key, cat_order.get(c.category, 99)))
    items.sort(key=lambda x: (x[4], x[0]))
    return items


class SlashCommandCompleter(Completer):
    def get_completions(self, document, _complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text:
            return
        text_lower = text.lower()
        from config.settings import get as _cfg_get
        for name, desc_key, args_hint, toggle_key, _order in _slash_commands():
            if not name.startswith(text_lower):
                continue
            if toggle_key:
                suffix = " [on]" if _cfg_get(toggle_key, False) else " [off]"
            elif args_hint:
                suffix = f" {args_hint}"
            else:
                suffix = ""
            yield Completion(
                name,
                start_position=-len(text),
                display=name + suffix,
                display_meta=_(desc_key),
            )


def make_combined_completer(working_dir: str = "."):
    file_completer = FileAtCompleter(working_dir=working_dir)
    slash_completer = SlashCommandCompleter()
    merged = merge_completers([slash_completer, file_completer])
    return merged, file_completer


class FileAtCompleter(Completer):
    def __init__(self, working_dir="."):
        self.working_dir = working_dir

    def set_working_dir(self, path):
        self.working_dir = path

    def get_completions(self, document, _complete_event):
        text = document.text
        cursor = document.cursor_position
        ref = _find_at_reference(text, cursor)
        if ref is None:
            return

        if ref == "" or "all".startswith(ref.lower()):
            yield Completion(
                "all",
                start_position=-len(ref),
                display="all",
                display_meta=_("ac.entire_project"),
            )

        base_dir = Path(self.working_dir)
        if "/" in ref:
            dir_part, filter_part = ref.rsplit("/", 1)
            scan_dir = base_dir / dir_part
            prefix = dir_part + "/"
        else:
            filter_part = ref
            scan_dir = base_dir
            prefix = ""
        if not scan_dir.is_dir():
            return
        entries = _scan_dir(scan_dir, prefix=prefix)
        filter_lower = filter_part.lower()
        count = 0
        for rel_path, is_dir in entries:
            name_part = rel_path.rstrip("/")
            if "/" in name_part:
                name_part = name_part.rsplit("/", 1)[-1]
            if filter_lower and not name_part.lower().startswith(filter_lower):  # noqa: SIM102
                if filter_lower not in name_part.lower():
                    continue
            start_position = -len(ref)
            if is_dir:
                display_text = rel_path
                dir_size = _dir_total_size(base_dir / rel_path)
                display_meta = _format_tokens(dir_size) if dir_size > 0 else _("ac.empty")
            else:
                display_text = rel_path
                try:
                    full_path = base_dir / rel_path
                    size = full_path.stat().st_size
                    display_meta = _format_size(size)
                except OSError:
                    display_meta = _("ac.file")
            yield Completion(
                rel_path,
                start_position=start_position,
                display=display_text,
                display_meta=display_meta,
            )
            count += 1
            if count >= _MAX_RESULTS:
                break

