"""Parser for the new fenced call-block tool format.

Sibling of the legacy parser in tools/parser.py.
Returns ToolCall objects with args compatible with execute_call().
"""

import html
import re

from logger import logger
from .models import ToolCall
from .json_repair import robust_json_loads as _robust_json_loads
from ._html_unescape import maybe_unescape as _maybe_unescape_html, has_html_entities as _has_html_entities

NAMED_TOOLS = frozenset({
    "read_files", "read_file", "write_file", "patch_file",
    "create_file", "delete_file", "rename_file", "copy_file",
    "move_file", "ls", "tree", "mkdir", "rmdir", "find_files",
    "grep_files", "poll", "skill", "shell", "web_search",
    "ssh", "subagent", "workflow", "create_docx", "docx_screenshot", "expand_tool_result", "apply_diff",
    "lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics",
})

_CONTENT_TOOLS = frozenset({"write_file", "create_file", "create_docx"})
_PATCH_TOOLS = frozenset({"patch_file"})
_DIFF_TOOLS = frozenset({"apply_diff"})

# Fence: :::call <tool> ... call:::
# Open  marker: ::: BEFORE 'call' (colons first).
# Close marker: ::: AFTER  'call' (colons last).
# Asymmetric: parser unambiguously distinguishes open from close.
_CALL_BLOCK_RE = re.compile(
    r":::call[ \t]+(?P<name>[a-zA-Z_]\w*)"
    r"(?P<attrs>[^\n]*)\n"
    r"(?P<body>.*?)"
    r"(?:\n|^)call:::[ \t]*(?:\n|$)",
    re.DOTALL | re.MULTILINE,
)

_CALL_BLOCK_TRUNCATED_RE = re.compile(
    r":::call[ \t]+(?P<name>[a-zA-Z_]\w*)"
    r"(?P<attrs>[^\n]*)\n"
    r"(?P<body>.*)\Z",
    re.DOTALL,
)

_STRIP_CALL_BLOCK_RE = re.compile(
    r":::call[ \t]+\w+[^\n]*\n"
    r".*?"
    r"(?:\n|^)call:::[ \t]*(?:\n|$)",
    re.DOTALL | re.MULTILINE,
)
_STRIP_CALL_TRUNCATED_RE = re.compile(
    r":::call[ \t]+\w+[^\n]*\n.*\Z",
    re.DOTALL,
)

_ATTR_RE = re.compile(
    r'(?P<key>[a-zA-Z_]\w*)\s*=\s*'
    r'(?:"(?P<qval>(?:[^"\\]|\\.)*)"|(?P<bval>\S+))'
)

_INT_ATTRS = frozenset({"line", "depth", "context", "max_results"})
_BOOL_ATTRS = frozenset({
    "all", "force", "ignore_case", "literal", "long", "fetch",
})

def _coerce_attr(key, val):
    if key in _INT_ATTRS:
        try:
            return int(val)
        except (TypeError, ValueError):
            return val
    if key in _BOOL_ATTRS:
        lv = val.lower()
        if lv in ("true", "1", "yes"):
            return True
        if lv in ("false", "0", "no"):
            return False
    return val

def _parse_attrs(header):
    attrs = {}
    if not header or not header.strip():
        return attrs
    for m in _ATTR_RE.finditer(header):
        key = m.group("key")
        if m.group("qval") is not None:
            val = m.group("qval").replace(r'\"', '"').replace(r"\\", "\\")
        else:
            val = m.group("bval")
        attrs[key] = _coerce_attr(key, val)
    return attrs

_SECTION_LINE_RE = re.compile(
    r"^[ \t]*---[ \t]+(FIND|REPLACE|INSERT)[ \t]+---[ \t]*$",
    re.MULTILINE,
)

_END_MARKER_RE = re.compile(
    r"^[ \t]*---[ \t]+END[ \t]+---[ \t]*$",
    re.MULTILINE,
)

# Известное ограничение маскировки: bare `call:::` внутри тела FIND/REPLACE
# (например когда патч редактирует САМ парсер и содержит литеральный маркер
# `call:::`) преждевременно закрывает блок — маска `_mask_code_regions`
# гасит только fenced/inline-code, а голый маркер в обычном тексте тела не
# отличить от настоящего закрывающего. Рискованный rewrite парсера здесь не
# делаем; обходной путь — оборачивать такой маркер в inline-бэктики.


# Маскирует содержимое code-регионов (fenced ```/~~~ блоки и inline-бэктики)
# пробелами той же длины, чтобы offset'ы в маске совпадали с оригиналом.
# Нужно чтобы маркеры :::call / call::: внутри code-span не воспринимались
# парсером как реальные tool-блоки (например когда модель объясняет синтаксис
# в обычном тексте через inline-code).
_FENCED_CODE_RE = re.compile(
    r"(?P<fence>^[ \t]*(?:`{3,}|~{3,}))[^\n]*\n.*?(?:^[ \t]*(?P=fence)[ \t]*(?:\n|$)|\Z)",
    re.DOTALL | re.MULTILINE,
)
# Inline-код по спецификации markdown не пересекает границы строк, поэтому
# НЕ используем DOTALL и запрещаем перевод строки внутри — иначе одиночный
# бэктик в обычном тексте мог бы «съесть» несколько строк (вместе с реальными
# :::call/call::: маркерами на них).
_INLINE_CODE_RE = re.compile(r"(?P<ticks>`+)(?!`)[^\n]+?(?P=ticks)")


def _mask_code_regions(text: str) -> str:
    if not text or ("`" not in text and "~" not in text):
        return text
    buf = list(text)

    def _blank(start: int, end: int) -> None:
        for i in range(start, min(end, len(buf))):
            if buf[i] != "\n":
                buf[i] = " "

    for m in _FENCED_CODE_RE.finditer(text):
        _blank(m.start(), m.end())
    masked = "".join(buf)
    for m in _INLINE_CODE_RE.finditer(masked):
        _blank(m.start(), m.end())
    return "".join(buf)


def _split_patch_sections(body):
    matches = list(_SECTION_LINE_RE.finditer(body))
    if not matches:
        return []
    sections = []
    for i, m in enumerate(matches):
        kind = m.group(1)
        start = m.end()
        if start < len(body) and body[start] == "\n":
            start += 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end]
        # Поддержка опционального терминатора '--- END ---' в конце секции
        # (модели часто пишут его по аналогии с FIND/REPLACE).
        end_m = _END_MARKER_RE.search(content)
        if end_m:
            content = content[:end_m.start()]
        if content.endswith("\n"):
            content = content[:-1]
        sections.append((kind, content))
    # Отбрасываем висячий пустой маркер в конце: модель иногда дублирует
    # '--- REPLACE ---' / '--- FIND ---' после контента (по инерции). Такая
    # секция идёт ПОСЛЕДНЕЙ и имеет пустой content — она не несёт смысла и
    # лишь засоряет пары FIND/REPLACE. Реальные пустые секции в середине
    # (например пустой REPLACE = удаление) сохраняем.
    while len(sections) > 1 and sections[-1][1] == "":
        sections.pop()
    return sections

def _build_patch_args(body, attrs):
    args = {}
    if "path" in attrs:
        args["path"] = attrs["path"]
    if "delete_lines" in attrs:
        args["delete_lines"] = attrs["delete_lines"]

    sections = _split_patch_sections(body)

    insert_section = next((c for k, c in sections if k == "INSERT"), None)
    if insert_section is not None:
        args["insert"] = _maybe_unescape_html(insert_section)
        if "line" in attrs:
            args["line"] = attrs["line"]
        return args if args else None

    pairs = []
    pending_find = None
    for kind, content in sections:
        if kind == "FIND":
            pending_find = content
        elif kind == "REPLACE":
            if pending_find is None:
                continue
            pairs.append({
                "find": _maybe_unescape_html(pending_find),
                "replace": _maybe_unescape_html(content),
            })
            pending_find = None

    if len(pairs) == 1:
        args["find"] = pairs[0]["find"]
        args["replace"] = pairs[0]["replace"]
    elif len(pairs) > 1:
        args["patches"] = pairs

    return args if args else None

def _strip_one_trailing_newline(s):
    return s[:-1] if s.endswith("\n") else s

def _parse_content_tool(body, attrs):
    if "path" not in attrs:
        return None
    content = _strip_one_trailing_newline(body)
    content = _maybe_unescape_html(content)
    args = {
        "path": attrs["path"],
        "content": content,
    }
    if "encoding" in attrs:
        args["encoding"] = attrs["encoding"]
    return args

def _parse_json_body(body):
    body = body.strip()
    if not body:
        return {}
    parsed = _robust_json_loads(body)
    if parsed is None:
        if _has_html_entities(body):
            decoded = html.unescape(body)
            parsed = _robust_json_loads(decoded)
    if parsed is None:
        return None
    # Фикс 1.4: даже когда JSON распарсился, его строковые значения могли
    # приехать html-эскейпленными (прокси OnlySQ эскейпит и аргументы tool_calls).
    # Канонический unescape_nested рекурсивно проходит dict/list/str.
    from tools._html_unescape import unescape_nested as _unescape_nested
    parsed = _unescape_nested(parsed)
    if not isinstance(parsed, dict):
        return {"value": parsed}
    return parsed

def _is_known_tool(tool_name: str) -> bool:
    if tool_name in NAMED_TOOLS:
        return True
    # Динамически зарегистрированные MCP-инструменты (mcp__<server>__<tool>)
    if tool_name.startswith("mcp__"):
        try:
            from tools.registry import TOOL_REGISTRY
            return tool_name in TOOL_REGISTRY
        except Exception:
            logger.debug("TOOL_REGISTRY lookup failed for %r", tool_name, exc_info=True)
            return False
    return False


def parse_call_block(tool_name, attrs_header, body, raw):
    if not _is_known_tool(tool_name):
        logger.warning(
            "parse_call_block: unknown tool '{}' (body_preview={!r})",
            tool_name, (body or "")[:120],
        )
        return None

    attrs = _parse_attrs(attrs_header)

    if tool_name in _CONTENT_TOOLS:
        args = _parse_content_tool(body, attrs)
        if args is None:
            json_args = _parse_json_body(body)
            if isinstance(json_args, dict) and "path" in json_args:
                args = json_args
                if attrs:
                    merged = dict(json_args)
                    merged.update({k: v for k, v in attrs.items() if k != "path"})
                    args = merged
    elif tool_name in _PATCH_TOOLS:
        args = _build_patch_args(body, attrs)
        if args is None and "path" in attrs:
            args = dict(attrs)
        if args is None or "path" not in args:
            json_args = _parse_json_body(body)
            if isinstance(json_args, dict) and "path" in json_args:
                if attrs:
                    merged = dict(json_args)
                    merged.update({k: v for k, v in attrs.items() if k != "path"})
                    args = merged
                else:
                    args = json_args
    elif tool_name in _DIFF_TOOLS:
        # Тело — unified diff. Path не обязателен в шапке (берётся из diff).
        args = dict(attrs) if attrs else {}
        args["diff"] = body or ""
    else:
        json_args = _parse_json_body(body)
        if json_args is None:
            # Пустое/невалидное тело — допускаем shorthand с path/args в attrs:
            # :::call delete_file path="x.py" / :::call ls path="dir" и т.п.
            if attrs:
                args = dict(attrs)
            else:
                return None
        else:
            if attrs:
                merged = dict(attrs)
                merged.update(json_args)
                args = merged
            else:
                args = json_args

    if args is None:
        logger.warning(
            "parse_call_block: tool '{}' parsed to None (body_preview={!r})",
            tool_name, (body or "")[:160],
        )
        return None

    # Content and patch tools REQUIRE path. Without it, the block is malformed.
    if tool_name in _CONTENT_TOOLS or tool_name in _PATCH_TOOLS:
        if not isinstance(args, dict) or not args.get("path"):
            logger.warning(
                "parse_call_block: tool '{}' missing 'path' in fence header",
                tool_name,
            )
            return None

    display = tool_name
    if isinstance(args, dict):
        if tool_name == "shell" and "command" not in args and "cmd" in args:
            cmd_val = args.pop("cmd")
            if isinstance(cmd_val, str):
                args["command"] = cmd_val
        if "path" in args:
            display = f"{tool_name} {args['path']}"
        elif tool_name == "shell":
            cmd_field = args.get("command")
            if isinstance(cmd_field, str):
                display = cmd_field

    return ToolCall(
        command=display,
        tool_name=tool_name,
        args=args if isinstance(args, dict) else {"value": args},
        raw=raw,
    )

def iter_call_blocks(text):
    masked = _mask_code_regions(text)
    for m in _CALL_BLOCK_RE.finditer(masked):
        # Берём содержимое из оригинала по offset'ам — длины совпадают.
        real = _CALL_BLOCK_RE.match(text, m.start())
        if real is None or real.end() != m.end():
            continue
        call = parse_call_block(
            real.group("name"),
            real.group("attrs"),
            real.group("body"),
            real.group(0),
        )
        yield real, call

def parse_call_calls(text):
    calls = []
    for _m, call in iter_call_blocks(text):
        if call is not None:
            calls.append(call)
    return calls

def strip_call_calls(text):
    # Находим спаны блоков по маске, удаляем из оригинала с конца.
    masked = _mask_code_regions(text)
    spans = []
    scan = 0
    for m in _STRIP_CALL_BLOCK_RE.finditer(masked):
        spans.append((m.start(), m.end()))
        scan = m.end()
    # Незакрытый (truncated) блок ищем только ПОСЛЕ последнего complete-блока,
    # иначе truncated-regex `:::call ... \Z` жадно захватит и хвостовой текст,
    # идущий за уже закрытым блоком.
    tm = _STRIP_CALL_TRUNCATED_RE.search(masked, scan)
    if tm:
        spans.append((tm.start(), tm.end()))
    if not spans:
        return text
    spans.sort(reverse=True)
    result = text
    for s, e in spans:
        result = result[:s] + result[e:]
    return result

def has_call_calls(text):
    return bool(_CALL_BLOCK_RE.search(_mask_code_regions(text)))

def find_next_complete_call(text, offset=0):
    masked = _mask_code_regions(text)
    m = _CALL_BLOCK_RE.search(masked, offset)
    if not m:
        return None
    real = _CALL_BLOCK_RE.match(text, m.start())
    if real is None or real.end() != m.end():
        return None
    return {
        "start": real.start(),
        "end": real.end(),
        "tool_name": real.group("name"),
        "attrs_header": real.group("attrs"),
        "body": real.group("body"),
        "raw": real.group(0),
    }

def find_next_partial_call(text, offset=0):
    masked = _mask_code_regions(text)
    scan = offset
    for c in _CALL_BLOCK_RE.finditer(masked, offset):
        scan = c.end()
    m = _CALL_BLOCK_TRUNCATED_RE.search(masked, scan)
    if not m:
        return None
    real = _CALL_BLOCK_TRUNCATED_RE.match(text, m.start())
    if real is None:
        return None
    return {
        "start": real.start(),
        "end": real.end(),
        "tool_name": real.group("name"),
        "attrs_header": real.group("attrs"),
        "body": real.group("body"),
        "raw": real.group(0),
    }

def find_next_call_start(text, offset=0):
    masked = _mask_code_regions(text)
    c = _CALL_BLOCK_RE.search(masked, offset)
    p = _CALL_BLOCK_TRUNCATED_RE.search(masked, offset)
    if c and p:
        return min(c.start(), p.start())
    if c:
        return c.start()
    if p:
        return p.start()
    return None
