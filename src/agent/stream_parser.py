"""Stream tool block scanning — finds complete/partial :::call ... call::: blocks in streaming text."""

import re
from dataclasses import dataclass

from agent.think import strip_partial_think_block, strip_think_blocks
from planner import strip_plan_commands
from tools import strip_tool_calls as _strip_tool_calls
from tools.call_parser import (
    find_next_call_start as _find_next_call_start_block,
)
from tools.call_parser import (
    find_next_complete_call as _find_next_complete_call_block,
)
from tools.call_parser import (
    find_next_partial_call as _find_next_partial_call_block,
)


@dataclass
class StreamToolMatch:
    start: int
    end: int
    tool_name: str
    body: str
    raw: str
    complete: bool
    attrs_header: str = ""

def _find_next_tool_start(text: str, offset: int) -> int | None:
    return _find_next_call_start_block(text, offset)

def _find_next_complete_tool(text: str, offset: int) -> StreamToolMatch | None:
    info = _find_next_complete_call_block(text, offset)
    if info is None:
        return None
    if info.get("tool_name") in ("plan", "think"):
        return None
    return StreamToolMatch(
        start=info["start"],
        end=info["end"],
        tool_name=info["tool_name"],
        body=info["body"],
        raw=info["raw"],
        complete=True,
        attrs_header=info.get("attrs_header", ""),
    )

def _find_next_partial_tool(text: str, offset: int) -> StreamToolMatch | None:
    info = _find_next_partial_call_block(text, offset)
    if info is None:
        return None
    if info.get("tool_name") in ("plan", "think"):
        return None
    return StreamToolMatch(
        start=info["start"],
        end=info["end"],
        tool_name=info["tool_name"],
        body=info["body"],
        raw=info["raw"],
        complete=False,
        attrs_header=info.get("attrs_header", ""),
    )

# Служебный маркер прокси (OnlySQ и пр.): "usem*resume*", "m*resume*",
# "*resume*" — сигнал «продолжай генерацию» между раундами выполнения
# инструментов. К ответу модели отношения не имеет, в терминал попадать
# не должен.
_PROXY_RESUME_RE = re.compile(r"\w*\*resume\*\s*", re.IGNORECASE)

# Транспортная обёртка прокси (OnlySQ и пр.): строка-преамбула
# "user Current date: <дата>" и теги <query>/</query> вокруг сообщения.
# К ответу модели отношения не имеет — в терминал и историю попадать
# не должна. Сущности (<query>) приезжают как из эскейпленного,
# так и из декодированного варианта — покрываем оба.
_PROXY_PREAMBLE_RE = re.compile(
    r"^[ \t]*(?:\[?user\]?)?[ \t]*current date:[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)
_PROXY_QUERY_TAG_RE = re.compile(
    r"^[ \t]*(?:<|<)/?query(?:>|>)[ \t]*\n?",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_proxy_markers(text: str) -> str:
    out = _PROXY_RESUME_RE.sub("", text)
    out = _PROXY_PREAMBLE_RE.sub("", out)
    out = _PROXY_QUERY_TAG_RE.sub("", out)
    return out


def _clean_display_text(text: str, strip_calls: bool = True) -> str:
    # strip_calls=False (native function-calling): fenced-блоки НЕ вырезаются —
    # модель вызывает инструменты через native tool_calls, а любой текст вида
    # :::call ... call::: в ответе печатается дословно как обычный текст.
    from agent.sanitizer import (
        _ROLE_LEAK_RE,
        strip_fake_runtime_tool_results,
        strip_fake_tool_output,
    )

    result = _strip_proxy_markers(text)
    result = strip_fake_runtime_tool_results(result)
    result = strip_fake_tool_output(result)
    result = _ROLE_LEAK_RE.sub("", result)
    result = strip_plan_commands(result)
    result = strip_think_blocks(result)
    result = strip_partial_think_block(result)
    result = re.sub(
        r"\n?[ \t]*:{2,3}call[ \t]+think(?:[ \t]+[^\n]*)?\Z",
        "",
        result,
    )
    if not strip_calls:
        # Native function-calling: fenced-вызовы не исполняются парсером и
        # печатаются как обычный текст — НЕ вырезаем call-блоки/маркеры.
        return re.sub(r"\n{3,}", "\n\n", result).strip()
    result = _strip_tool_calls(result)
    # Снимаем хвостовой осколок незакрытого fence: `:::call <tool> attrs...`
    # БЕЗ финального \n. Это критично для моделей с честным мелко-чанковым
    # SSE (GPT и др.): такая строка не матчится TRUNCATED_RE (требует \n
    # после attrs), партиал-detect её не видит, и она утекает в Live /
    # BlockStreamer как plain text → «голые заголовки» над панелью.
    # Регекс делаем ДО strip-маркеров: иначе они съедят `:::call ` префикс
    # и оставят `<tool> attrs...`, который уже не выглядит как fence.
    result = re.sub(
        r"\n?[ \t]*:{2,3}call[ \t]+[a-zA-Z_]\w*(?:[ \t]+[^\n]*)?\Z",
        "",
        result,
    )
    # Снимаем самостоятельные маркеры открытия/закрытия (без атрибутов).
    result = re.sub(r"^\s*(?::{2,3}call|call:{2,3})\s*\n?", "", result)
    result = re.sub(r"\n?\s*(?::{2,3}call|call:{2,3})\s*$", "", result)
    # Голые осколки маркеров на отдельной строке: `:::`, `::`, `call::`, `call:`.
    # Появляются при мелко-чанковом SSE, когда между tool-блоками частично
    # приехавший закрывающий/открывающий маркер не дозрел до полного.
    result = re.sub(
        r"(?m)^[ \t]*(?::{1,3}|call:{1,3}|:{1,3}call)[ \t]*$\n?",
        "",
        result,
    )
    return re.sub(r"\n{3,}", "\n\n", result).strip()
