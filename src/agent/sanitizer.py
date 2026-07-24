"""Санитизация ответов модели: удаление фейковых tool_result."""

import re

from logger import logger
from tools._html_unescape import maybe_unescape as _maybe_unescape

# XML-артефакты от Anthropic-style tool-calling формата, которые модель иногда
# вставляет в текст. Удаляются до парсинга, иначе могут ломать fenced-блоки.
# Один общий паттерн вместо шести — покрывает оба варианта префикса (с antml:
# и без), и три типа тегов в одной альтернативе.
_XML_TOOL_ARTIFACTS = [
    re.compile(
        r"</?(?:antml:)?(?:parameter|invoke|function_calls)(?:\s+[^>]*)?>",
        re.IGNORECASE,
    ),
]

# Прокси (OnlySQ и пр.) иногда инжектят транспортную обёртку прямо в текст
# ответа модели: строку-преамбулу "[user]Current date: <дата>" и теги
# <query>...</query>. Это не часть ответа — вырезаем строку с датой
# и сами теги query (в т.ч. html-эскейпленные <query>).
_PROXY_WRAP_RE = re.compile(
    r"^[ \t]*(?:user)?[ \t]*current date:[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)
_PROXY_QUERY_TAG_RE = re.compile(
    r"</?query>",
    re.IGNORECASE,
)

_UNCLOSED_CALL_FENCE_RE = re.compile(
    r":::call[ \t]+[a-zA-Z_]\w*[^\n]*\n"
    r"(?P<body>(?:(?!call:::).)*)\Z",
    re.DOTALL,
)

# Кривой fence-маркер: модель иногда пишет открытие с 1-2 двоеточиями вместо
# трёх (`::call memory_list`) или закрытие `call::`/`:call`. Настоящий блок
# (`:::call … call:::`) к этому моменту уже защищён плейсхолдером, поэтому
# здесь матчатся ТОЛЬКО сломанные маркеры — их вырезаем целиком вместе с
# json-телом, чтобы они не утекли в вывод как мусор (`⏺ Tool (no args)`).
# Граница строки `(?<![:])` слева не даёт задеть валидный `:::call`.
_MALFORMED_CALL_OPEN_RE = re.compile(
    r"(?m)^[ \t]*:{1,2}call[ \t]+[a-zA-Z_]\w*[^\n]*\n"
    r"(?:[ \t]*\{.*?\}[ \t]*\n?)?"      # опц. однострочное/многострочное json-тело
    r"(?:[ \t]*:{0,3}call:{0,3}[ \t]*\n?)?",  # опц. кривое закрытие (call::, :call:::)
    re.DOTALL,
)
# Одиночный осколок кривого закрытия на своей строке (`call::`, `:call:`).
_MALFORMED_CALL_CLOSE_RE = re.compile(
    r"(?m)^[ \t]*(?::{1,2}call:{1,3}|call:{1,2})[ \t]*$\n?",
)


def _close_unclosed_call_fence(text: str) -> str:
    """Если в конце текста остался незакрытый :::call блок — закрыть его."""
    m = _UNCLOSED_CALL_FENCE_RE.search(text)
    if not m:
        return text
    suffix = "\ncall:::" if not text.endswith("\n") else "call:::"
    logger.warning(
        "sanitize_response: closing unclosed call fence (body_len={})",
        len(m.group("body") or ""),
    )
    return text + suffix + "\n"


# Эти паттерны выполняются ПОСЛЕ _protect_call_blocks: корректные
# :::call ... call::: блоки уже заменены плейсхолдером \x00CALL_BLOCK_N\x00.
# Поэтому «после tool-вызова» = после плейсхолдера (а НЕ после литерального
# call:::, которого в этот момент в тексте уже нет). Раньше якорь был
# `call:::` — и фейк-вывод никогда не вычищался, т.к. _protect_call_blocks
# съедал call::: в плейсхолдер. Допускаем и литеральный call::: на случай
# незакрытого/неполного блока, который не попал под protect.
_AFTER_CALL = r"(?:\x00CALL_BLOCK_\d+\x00|call:::)"
_NEXT_CALL = r"(?:\n\s*:::call\b|\x00CALL_BLOCK_\d+\x00|\Z)"

_FAKE_RESULT_PATTERNS = [
    # ... — основной паттерн галлюцинаций
    re.compile(r"<tool_result[^>]*>.*?</tool_result>", re.DOTALL),
    #  без закрывающего тега (обрезанный)
    re.compile(r"<tool_result[^>]*>(?:(?!</tool_result>).)*$", re.DOTALL),
    # <tool_output>…</tool_output> — обёртка, которой build_tool_results
    # помечает реальный системный вывод. Модель (opus 4.8) может начать её
    # имитировать в ответе — вырезаем целиком, как tool_result.
    re.compile(r"<tool_output[^>]*>.*?</tool_output>", re.DOTALL | re.IGNORECASE),
    # …без закрывающего тега (обрезанный реплей)
    re.compile(r"<tool_output[^>]*>(?:(?!</tool_output>).)*$", re.DOTALL | re.IGNORECASE),
    # Фейковые "File created successfully" строки
    re.compile(
        r"\n\s*(?:<[^>]*>)?\s*File (?:created|written|saved|updated|deleted|moved|copied|renamed)\s+successfully[^\n]*",
        re.IGNORECASE,
    ),
    # Фейковые строки с ✓ Created/Written/etc после tool-вызова.
    # Якорь в группе 1 — _replace вернёт его, плейсхолдер не потеряется.
    re.compile(
        "(" + _AFTER_CALL + r")\s*\n+\s*(?:✓|✔|√)\s*(?:Created|Written|Saved|Updated|Deleted|Renamed|Copied|Moved|Создан|Записан|Удалён|Обновлён)[^\n]*",
        re.IGNORECASE,
    ),
    # Блоки "Output:" / "Result:" / "Вывод:" после tool-вызова
    re.compile(
        "(" + _AFTER_CALL + r")\s*\n+\s*(?:Output|Result|Результат|Вывод)\s*[:\-]\s*\n.*?(?=" + _NEXT_CALL + ")",
        re.DOTALL | re.IGNORECASE,
    ),
    # Модель реплеит proxy/user turn после tool-call: Current date + <query> + fake output.
    re.compile(
        "(" + _AFTER_CALL + r")\s*\n+\s*(?:user\s+)?Current date:.*?</query>\s*",
        re.DOTALL | re.IGNORECASE,
    ),
    # Фейковый transcript после tool-call: `$ cmd`, `user$ cmd`, `usuario$ cmd`,
    # `[file lines ...]`, `[Project: ...]`, bullet+separator blocks.
    re.compile(
        "(" + _AFTER_CALL + r")\s*\n+"
        r"(?:[ \t]*(?:●\s*)?-{3,}[ \t]*\n)?"
        r"(?:"
        r"(?:[^\s$]{1,32})?\$[^\n]*\n"
        r"|(?:\[[^\]\n]*(?:lines\s+\d+|no matches|Project:)[^\]\n]*\]\n)"
        r"|(?:●\s+[^\n]*(?:\$\s*|/[^\n]*:\d+:\d+)[^\n]*\n)"
        r"|(?:-{20,}\n)"
        r"|(?:\d+:\s+[^\n]*\n)"
        r"|(?:[^\n]*(?:✓|✔|√)[^\n]*\n)"
        r")+",
        re.IGNORECASE | re.MULTILINE,
    ),
]

# Утёкший role-токен от native↔fenced смешения (opus 4.8): модель иногда
# начинает новую реплику маркером роли прямо в тексте. Видели как полный
# `assistant`, так и огрызок `ass`, слитно со следующим словом, начинающимся
# с заглавной (`assПрочитал`, `assistantProcessing`). Срезаем ТОЛЬКО когда:
#   - в начале строки (^),
#   - префикс роли (ass[istant]) ВПЛОТНУЮ примыкает к заглавной букве
#     (латиница/кириллица) — обычные слова так не пишутся.
# Сам префикс убираем, дальнейший текст («Прочитал…») сохраняем.
_ROLE_LEAK_RE = re.compile(
    r"(?m)^[ \t]*(?:assistant|assistan|assista|assist|assis|assi|ass)(?=[A-ZА-ЯЁ])",
)




_CALL_BLOCK_FOR_SANITIZE_RE = re.compile(
    r":::call[ \t]+[a-zA-Z_]\w*[^\n]*\n"
    r".*?"
    r"(?:\n|^)call:::[ \t]*(?:\n|$)",
    re.DOTALL | re.MULTILINE,
)


def _protect_call_blocks(text: str) -> tuple[str, list[str]]:
    """Вырезает :::call ... call::: блоки, заменяя плейсхолдерами.

    Содержимое call-блоков — это аргументы инструментов (HTML для create_docx,
    код для write_file и т.п.), их нельзя пропускать через sanitizer-фильтры
    (HTML-стрип, fake-result regex), иначе HTML-теги внутри content будут
    удалены и инструмент получит сломанный ввод.
    """
    stored: list[str] = []

    def _sub(m: re.Match) -> str:
        block = m.group(0)
        stored.append(block)
        # Блок-regex захватывает завершающий `\n` после call:::. Если его
        # съесть в плейсхолдер, следующая строка (фейк-transcript `● ---`,
        # `$ cmd`) приклеится к плейсхолдеру → ломаются построчные/якорные
        # фильтры. Возвращаем `\n` обратно, чтобы плейсхолдер стоял на своей
        # строке.
        tail = "\n" if block.endswith("\n") else ""
        return f"\x00CALL_BLOCK_{len(stored) - 1}\x00" + tail

    return _CALL_BLOCK_FOR_SANITIZE_RE.sub(_sub, text), stored


def _restore_call_blocks(text: str, stored: list[str]) -> str:
    for i, block in enumerate(stored):
        text = text.replace(f"\x00CALL_BLOCK_{i}\x00", block)
    return text


_RUNTIME_TOOL_RESULTS_RE = re.compile(
    r"<runtime_tool_results(?:_summary)?\b[\s\S]*?</runtime_tool_results(?:_summary)?>",
    re.IGNORECASE,
)
_RUNTIME_TOOL_RESULTS_SUMMARY_INLINE_RE = re.compile(
    r"<runtime_tool_results_summary\b[^>]*/?>",
    re.IGNORECASE,
)

def strip_fake_runtime_tool_results(text: str) -> str:
    """Удаляет runtime_tool_results-блоки, если модель сымитировала системный вывод."""
    if not text:
        return text
    text = _RUNTIME_TOOL_RESULTS_RE.sub("", text)
    text = _RUNTIME_TOOL_RESULTS_SUMMARY_INLINE_RE.sub("", text)
    return text

_REPLAY_START_RE = re.compile(
    r"^\s*●\s*-{3,}\s*$"
    r"|^\s*(?:[●]\s*)?(?:[^\s$]{1,32})?\$[^\n]*$"
    r"|^\s*\[[^\]\n]*(?:lines\s+\d+|no matches|Project:)[^\]\n]*\]\s*$"
    r"|^\s*●\s+[^\n]*(?:\$|/[^\n]*:\d+:\d+)"
    r"|^\s*(?:user\s+)?Current date:",
    re.IGNORECASE,
)
_STRUCTURED_REPLAY_LINE_RE = re.compile(
    r"^\s*$"
    r"|^\s*\[[^\]\n]*(?:lines\s+\d+|no matches|Project:)[^\]\n]*\]\s*$"
    r"|^\s*\d+:\s"
    r"|^\s*\.{3}\s*\(truncated\)"
    r"|^\s*(?:✓|✔|√)\s"
    r"|^\s*-{3,}\s*$",
    re.IGNORECASE,
)
_WHOLE_REPLAY_HINT_RE = re.compile(
    r"(?m)^\s*-{20,}\s*$"
    r"|\[Project:"
    r"|(?s:^\s*(?:[●]\s*)?(?:[^\s$]{1,32})?\$.*\n.*^\s*(?:[●]\s*)?(?:[^\s$]{1,32})?\$)"
    r"|Plan \[\d+/\d+\]"
    r"|Key reminders:"
    r"|tool calls have produced"
    r"|Continue now",
    re.IGNORECASE,
)

def _is_call_anchor(line: str) -> bool:
    return "\x00CALL_BLOCK_" in line or line.strip() == "call:::"

def _next_call_index(lines: list[str], start: int) -> int:
    for i in range(start, len(lines)):
        stripped = lines[i].lstrip()
        if "\x00CALL_BLOCK_" in lines[i] or stripped.startswith(":::call"):
            return i
    return len(lines)

def _strip_replayed_transcript(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        out.append(lines[i])
        if not _is_call_anchor(lines[i]):
            i += 1
            continue

        j = i + 1
        blanks: list[str] = []
        while j < len(lines) and not lines[j].strip():
            blanks.append(lines[j])
            j += 1
        if j >= len(lines):
            out.extend(blanks)
            i = j
            continue

        first = lines[j]
        if not _REPLAY_START_RE.search(first):
            out.extend(blanks)
            i = j
            continue

        next_call = _next_call_index(lines, j)
        block = "".join(lines[j:next_call])
        if re.search(r"^\s*(?:user\s+)?Current date:", first, re.IGNORECASE):
            i = next_call
            continue

        if _WHOLE_REPLAY_HINT_RE.search(block):
            last_marker = j
            for k in range(j, next_call):
                line = lines[k]
                if (
                    _REPLAY_START_RE.search(line)
                    or _STRUCTURED_REPLAY_LINE_RE.search(line)
                    or "$" in line
                    or "[Project:" in line
                    or "tool calls have produced" in line
                    or "Key reminders:" in line
                    or "Continue now" in line
                    or line.lstrip().startswith("```call")
                ):
                    last_marker = k
            i = last_marker + 1
            continue

        k = j + 1
        while k < next_call and _STRUCTURED_REPLAY_LINE_RE.search(lines[k]):
            k += 1
        if k < next_call and _REPLAY_START_RE.search(lines[k]):
            i = next_call
            continue
        i = k
    return "".join(out)

def strip_fake_tool_output(text: str) -> str:
    """Совместимый helper: удаляет фейковый tool output, сохраняя реальные call-блоки."""
    if not text:
        return text
    result, call_blocks = _protect_call_blocks(text)
    result = _strip_replayed_transcript(result)

    for pattern in _FAKE_RESULT_PATTERNS:
        def _replace(m):
            groups = m.groups()
            if groups and groups[0]:
                return groups[0]
            return ""

        result = pattern.sub(_replace, result) if pattern.groups > 0 else pattern.sub("", result)
    return _restore_call_blocks(result, call_blocks)

def sanitize_response(text: str) -> str:
    """Удаляет фейковые tool_result и предсказанный вывод из ответа модели."""
    if not text:
        return text

    result = _maybe_unescape(text)
    result = strip_fake_runtime_tool_results(result)

    # Proxy-конверт внутри transcript replay (`Current date` + `<query>`) должен
    # дойти до replay-stripper ниже: если снять маркеры здесь, останется голый
    # фейк-вывод без признака начала блока.
    # Срезаем утёкший role-токен в начале строки (opus 4.8: `assПрочитал…`).
    # До protect — но префикс `ass`+Заглавная не встречается в коде/HTML
    # внутри call-блоков, так что риска задеть аргументы инструментов нет.
    result = _ROLE_LEAK_RE.sub("", result)

    # Удаляем XML-артефакты от Anthropic tool-calling формата
    for pattern in _XML_TOOL_ARTIFACTS:
        result = pattern.sub("", result)

    # Закрываем незакрытые :::call ... call::: блоки (если модель оборвалась)
    result = _close_unclosed_call_fence(result)

    # Защищаем содержимое корректных call-блоков от HTML-стриппинга и
    # фильтров fake-result — там HTML-теги могут быть валидным content
    # (например для create_docx) или частью кода.
    result, _call_blocks = _protect_call_blocks(result)

    # Вырезаем КРИВЫЕ fence-маркеры (1-2 двоеточия вместо трёх): валидные блоки
    # уже стали плейсхолдерами выше, так что под эти паттерны попадают только
    # сломанные `::call tool {…}` / осколки `call::`, которые модель написала
    # как текст. Иначе они утекают в рендер как `⏺ Tool (no args)`.
    result = _MALFORMED_CALL_OPEN_RE.sub("", result)
    result = _MALFORMED_CALL_CLOSE_RE.sub("", result)

    # Удаляем HTML-теги если они попали в ответ
    # SVG блоки целиком
    result = re.sub(r'<svg[^>]*>.*?</svg>', '', result, flags=re.DOTALL)
    # Открывающие и закрывающие теги
    result = re.sub(r'</?(?:div|span|pre|button|code|path|a|img|br|hr|p|ul|ol|li|table|tr|td|th|thead|tbody|h[1-6])[^>]*>', '', result)

    result = _strip_replayed_transcript(result)

    for pattern in _FAKE_RESULT_PATTERNS:

        def _replace(m):
            groups = m.groups()
            if groups and groups[0]:
                return groups[0]
            return ""

        result = pattern.sub(_replace, result) if pattern.groups > 0 else pattern.sub("", result)

    # Схлопываем пустые строки, пока тела tool-вызовов ещё защищены: формат
    # create_file/create_docx и patch_file значим, его нельзя нормализовать.
    result = re.sub(r"\n{3,}", "\n\n", result)

    # Восстанавливаем защищённые call-блоки
    result = _restore_call_blocks(result, _call_blocks)

    # Обычные proxy-маркеры вне replay-блоков всё равно не должны попасть в ответ.
    from agent.stream_parser import _strip_proxy_markers
    result = _strip_proxy_markers(result)
    result = _PROXY_WRAP_RE.sub("", result)
    result = _PROXY_QUERY_TAG_RE.sub("", result)

    final = result.strip()
    if len(text) - len(final) > 50:
        logger.debug(
            "sanitize_response: removed {} chars (from {} → {})",
            len(text) - len(final), len(text), len(final),
        )
    return final
