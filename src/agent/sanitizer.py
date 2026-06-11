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
    # Фейковый вывод "$ command\noutput" после tool-вызова. Покрывает
    # галлюцинацию opus 4.8: после :::call read_files модель предсказывает
    # свой же результат строкой `$ read_files ...` + нумерованными строками
    # файла. Сохраняем сам плейсхолдер (group 1), вырезаем фейк-вывод.
    # Допускаем префикс роли `user`/`assistant`/`●` ПЕРЕД `$` (opus 4.8 шлёт
    # `user$ grep ...`) — иначе слово `user` между call::: и `$` ломало якорь.
    re.compile(
        "(" + _AFTER_CALL + r")\s*\n+[ \t]*(?:●[ \t]*)?(?:user|assistant)?\$\s+[^\n]+\n(?:(?!:::call\b|\x00CALL_BLOCK_\d+\x00).)*?(?=" + _NEXT_CALL + ")",
        re.DOTALL | re.IGNORECASE,
    ),
    # Фейковый вывод вида `[path lines N-M of T]` + нумерованные строки
    # СРАЗУ после tool-вызова, без префикса `$ cmd` (opus 4.8 иногда
    # опускает строку команды). Якорим на плейсхолдере tool-вызова.
    re.compile(
        "(" + _AFTER_CALL + r")\s*\n+\s*\[[^\]\n]*\blines?\b[^\]\n]*\]\n(?:[ \t]*\d+:[^\n]*\n?)+",
        re.DOTALL,
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


# Live-вариант фейк-вывода: в стрим-буфере call-блоки ещё литеральные
# (call:::), плейсхолдеров нет. Якорим на `call:::` и срезаем предсказанный
# моделью результат — `$ cmd`+вывод или `[... lines N-M ...]`+нумерованные
# строки. Используется в _clean_display_text ДО strip_tool_calls, чтобы
# мусор не утёк в scrollback через BlockStreamer (sanitize_response чистит
# только сохранённый буфер, не перерисовывает уже напечатанное).
# Структура фейк-вывода: после call::: идёт опциональная строка `$ cmd`,
# затем заголовок `[path lines N-M of T]`, затем нумерованные строки файла
# (`70: ...`), пустые строки и маркер `... (truncated)`. Прозу (обычный
# ответ модели) НЕ трогаем — поэтому тело состоит ТОЛЬКО из output-образных
# строк, и матч обрывается на первой прозаической строке.
_LIVE_FAKE_READ_RE = re.compile(
    r"(call:::[ \t]*\n)[ \t]*\n+"
    r"(?:[ \t]*\$\s+[^\n]+\n)?"                     # опц. строка команды
    r"[ \t]*\[[^\]\n]*\blines?\b[^\]\n]*\][ \t]*\n"  # обязат. `[… lines …]`
    # Тело файла после заголовка: модель реплеит СОДЕРЖИМОЕ файла, которое
    # не обязано быть нумерованным (`def foo():`, `class Bar:` и т.п.) и может
    # содержать ПУСТЫЕ строки. Заголовок `[… lines …]` уже однозначно метит
    # фейк-вывод, поэтому поглощаем всё до следующего :::call или конца текста
    # (пустые строки внутри тела НЕ останавливают срез).
    r"(?:(?![ \t]*:::call\b)[^\n]*\n?)*",
    re.MULTILINE,
)

# Фейк-вывод shell без `[… lines …]`: `$ cmd` + произвольные строки вывода
# до пустой строки (после неё обычно идёт проза-ответ). Тело — любые строки
# КРОМЕ пустой и КРОМЕ начала нового tool-вызова `:::call`.
_LIVE_FAKE_SHELL_RE = re.compile(
    r"(call:::[ \t]*\n)[ \t]*\n+"
    r"[ \t]*(?:●[ \t]*)?(?:user|assistant)?\$\s+[^\n]+\n"   # `$ cmd` / `user$ cmd`
    r"(?:(?![ \t]*\n|[ \t]*:::call\b)[^\n]*\n?)*",          # вывод до пустой строки
    re.MULTILINE | re.IGNORECASE,
)

# Старт фейк-transcript-строки: опц. bullet `●`, опц. РОЛЕВОЙ ПРЕФИКС вплотную
# к `$`, затем `$ <cmd>`. Префикс — любое одиночное слово (вкл. локализованные
# `user`: usuario/utilisateur/benutzer/пользователь и т.п.) ВПЛОТНУЮ перед `$`.
# `\$\s+\S` (доллар + пробел + токен) — форма shell-приглашения, не валюта
# (`$5` не матчится: нет пробела). После tool-вызова `word$ cmd` — однозначно
# реплей, в нормальной прозе не встречается.
_FAKE_TRANSCRIPT_START_RE = re.compile(
    # Ролевой префикс перед `$` — ТОЛЬКО одно слово из букв (`user`/`assistant`/
    # локализованные). Раньше `[^\s$]{1,20}` хватал прозу с пунктуацией перед
    # `$ ` (напр. `…стоит около: $ 5`). Класс `[^\W\d_]` (буквы любого языка,
    # без цифр/подчёркивания/пунктуации) делает якорь консервативнее.
    r"^[ \t]*(?:●[ \t]*)?(?:[^\W\d_]{1,15})?\$[ \t]+\S+",
)
_FAKE_TRANSCRIPT_SEPARATOR_RE = re.compile(r"^\s*[─\-]{8,}\s*$")
_FAKE_TRANSCRIPT_META_RE = re.compile(
    r"^\s*(?:\[[^\]]*(?:Project|This step|image attached|lines?|files?)[^\]]*\]|[✓✗❌]\s|\d+\s|[📄🔍🔎]\s)",
    re.IGNORECASE,
)


def _looks_like_fake_transcript_start(line: str) -> bool:
    return bool(_FAKE_TRANSCRIPT_START_RE.match(line))


# Лид-ин фейкового transcript-а: маркер пункта `●` (один, опц. с хвостом)
# или короткое тире-правило `---`/`— ` которое opus 4.8 ставит перед `$ cmd`.
_FAKE_TRANSCRIPT_LEADIN_RE = re.compile(r"^\s*(?:●\s*)?[─\-—]{2,}\s*$|^\s*●\s*$")

# Строка-вывод инструмента в фейк-реплее, начинающаяся с `●` + путь/результат
# БЕЗ `$` (например вывод lsp_definition: `● /abs/path/file.py:1:5`). Такой
# строкой реплей может НАЧАТЬСЯ, поэтому её пропускаем при поиске сигнала.
_FAKE_TRANSCRIPT_PATH_LINE_RE = re.compile(r"^\s*●\s*\S*[/\\]\S+")

# Прокси-конверт как НАЧАЛО фейк-turn-а: модель реплеит транспортную обёртку
# (`user Current date: …`, `<query>`) которой её обучает BASE_HEADER. После
# tool-вызова это однозначный признак галлюцинации следующего хода.
_FAKE_TRANSCRIPT_PROXY_RE = re.compile(
    r"^[ \t]*(?:\[?user\]?[ \t]*)?current date:|^[ \t]*</?query>[ \t]*$",
    re.IGNORECASE,
)


def _looks_like_fake_transcript_body(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _looks_like_fake_transcript_start(line):
        return True
    if _FAKE_TRANSCRIPT_SEPARATOR_RE.match(line):
        return True
    if _FAKE_TRANSCRIPT_LEADIN_RE.match(line):
        return True
    if _FAKE_TRANSCRIPT_META_RE.match(line):
        return True
    return False


# Якорь конца tool-блока (плейсхолдер ИЛИ литеральный call:::) для построчного
# вырезания фейк-transcript-а, идущего ПОСЛЕ вызова. Литеральный — на случай,
# когда блок не попал под _protect_call_blocks (например незакрытый).
_CALL_ANCHOR_LINE_RE = re.compile(r"^[ \t]*(?:\x00CALL_BLOCK_\d+\x00|call:::)[ \t]*$")
_REAL_CALL_LINE_RE = re.compile(r"^[ \t]*(?::::call\b|\x00CALL_BLOCK_\d+\x00)")


def _strip_fake_transcript_after_call(text: str) -> str:
    """Вырезает фейковый transcript, который модель дописала ПОСЛЕ tool-вызова.

    opus 4.8 в fenced иногда после `call:::` начинает реплеить весь раунд
    результатов: опц. лид-ин `● ---`, затем строки `$ cmd ✓ output` (команда и
    вывод НА ОДНОЙ строке), длинные тире-разделители, `[Project: …]`. Старые
    regex якорились на `\n+\s*$` и ломались об `● ---` лид-ин и same-line вывод.

    Построчно: находим строку-якорь конца вызова, пропускаем лид-ин/пустые,
    и если дальше пошёл fake-transcript (`$ cmd`/`user$`/разделитель/meta) —
    срезаем его до конца текста ИЛИ до следующего реального `:::call`
    (модель могла возобновить настоящую работу — её сохраняем).
    """
    if not text or ("call:::" not in text and "\x00CALL_BLOCK_" not in text):
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        out.append(lines[i])
        if not _CALL_ANCHOR_LINE_RE.match(lines[i].rstrip("\n")):
            i += 1
            continue
        # После якоря ищем сигнал fake-transcript в небольшом окне, пропуская
        # пустые / лид-ин (`● ---`) / bullet-path строки (вывод lsp-def:
        # `● /path:1:5`). Сигнал — ЛИБО `$ cmd`-старт, ЛИБО длинная тире-линия
        # (8+ `-`/`─`): и то, и другое не встречается в обычной прозе модели
        # сразу после tool-вызова. Реплей может начинаться с вывода любого
        # инструмента (не обязательно с `$`), поэтому одного `$`-старта мало.
        j = i + 1
        signal = -1
        while j < n:
            ln = lines[j]
            if not ln.strip() or _FAKE_TRANSCRIPT_LEADIN_RE.match(ln) or _FAKE_TRANSCRIPT_PATH_LINE_RE.match(ln):
                j += 1
                continue
            if (
                _looks_like_fake_transcript_start(ln)
                or _FAKE_TRANSCRIPT_SEPARATOR_RE.match(ln)
                or _FAKE_TRANSCRIPT_PROXY_RE.match(ln)
            ):
                signal = j
            break
        if signal < 0:
            i += 1
            continue
        j = i + 1  # срез начинаем сразу после якоря (вкл. лид-ин/path-строки)
        # Это фейк-transcript. После tool-вызова модель НЕ должна писать `$ cmd`
        # как текст — раз начала, весь хвост до следующего РЕАЛЬНОГО `:::call`
        # (или EOF) является галлюцинацией реплея результатов, включая
        # произвольный многострочный «вывод» (сигнатуры hover, тело файлов).
        # Поэтому поглощаем жадно до реального вызова/конца, не пытаясь
        # отличать «фейк-вывод» от «прозы» построчно (вывод hover/файла —
        # обычная проза и раньше обрывала срез слишком рано).
        k = j
        while k < n and not _REAL_CALL_LINE_RE.match(lines[k]):
            k += 1
        logger.warning(
            "sanitize_response: stripped fake transcript after call ({} lines)",
            k - i - 1,
        )
        i = k  # пропускаем [i+1 .. k-1]; продолжаем с k (реальный вызов/EOF)
    return "".join(out)


def strip_leading_fake_tool_transcript(text: str) -> str:
    """Срезает fake transcript-блоки (`$ tool...`) в начале фрагмента.

    Сохраняет нормальный текст после transcript-а: `Осталось...`, summary и т.п.
    """
    if not text:
        return text

    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not _looks_like_fake_transcript_start(lines[i]):
        return text

    while i < len(lines):
        if _looks_like_fake_transcript_start(lines[i]):
            i += 1
            while i < len(lines) and _looks_like_fake_transcript_body(lines[i]):
                i += 1
            continue
        if _looks_like_fake_transcript_body(lines[i]):
            i += 1
            continue
        break

    return "".join(lines[i:]).lstrip()


def strip_fake_tool_output(text: str) -> str:
    """Срезает предсказанный моделью «вывод инструмента» сразу после call:::.

    Для live-рендера (BlockStreamer): sanitize_response применяется только
    к финальному буферу истории и не стирает уже напечатанный scrollback,
    поэтому фейк-вывод нужно резать и на лету. Сохраняем сам `call:::`
    (group 1), вырезаем только предсказанный вывод.
    """
    text = _LIVE_FAKE_READ_RE.sub(lambda m: m.group(1), text)
    text = _LIVE_FAKE_SHELL_RE.sub(lambda m: m.group(1), text)
    # Жадно срезаем фейк-transcript (`● ---` + `$ cmd ✓ output` + разделители),
    # дописанный после литерального call::: — _CALL_ANCHOR_LINE_RE ловит и
    # литеральный маркер, не только плейсхолдер.
    text = _strip_fake_transcript_after_call(text)
    text = strip_leading_fake_tool_transcript(text)
    return text


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


def sanitize_response(text: str) -> str:
    """Удаляет фейковые tool_result и предсказанный вывод из ответа модели."""
    if not text:
        return text

    result = _maybe_unescape(text)

    # Служебный proxy-маркер "*resume*" (OnlySQ и пр.) не должен попадать
    # ни в терминал, ни в сохранённую историю.
    from agent.stream_parser import _strip_proxy_markers
    result = _strip_proxy_markers(result)

    result = _PROXY_WRAP_RE.sub("", result)
    result = _PROXY_QUERY_TAG_RE.sub("", result)

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

    # Удаляем HTML-теги если они попали в ответ
    # SVG блоки целиком
    result = re.sub(r'<svg[^>]*>.*?</svg>', '', result, flags=re.DOTALL)
    # Открывающие и закрывающие теги
    result = re.sub(r'</?(?:div|span|pre|button|code|path|a|img|br|hr|p|ul|ol|li|table|tr|td|th|thead|tbody|h[1-6])[^>]*>', '', result)

    for pattern in _FAKE_RESULT_PATTERNS:

        def _replace(m):
            groups = m.groups()
            if groups and groups[0]:
                return groups[0]
            return ""

        if pattern.groups > 0:
            result = pattern.sub(_replace, result)
        else:
            result = pattern.sub("", result)

    # Построчно вырезаем фейк-transcript, дописанный ПОСЛЕ tool-вызова
    # (`● ---` лид-ин, `$ cmd ✓ output` на одной строке, тире-разделители,
    # `[Project: …]`). Запускаем ПОКА плейсхолдеры call-блоков на месте —
    # якорь _CALL_ANCHOR_LINE_RE ловит и плейсхолдер, и литеральный call:::.
    result = _strip_fake_transcript_after_call(result)

    # Восстанавливаем защищённые call-блоки
    result = _restore_call_blocks(result, _call_blocks)

    # Очищаем множественные пустые строки
    result = re.sub(r"\n{3,}", "\n\n", result)
    final = result.strip()
    if len(text) - len(final) > 50:
        logger.debug(
            "sanitize_response: removed {} chars (from {} → {})",
            len(text) - len(final), len(text), len(final),
        )
    return final
