"""read_files — чтение одного или нескольких файлов.

Поддерживает текстовые форматы, CSV/TSV, Excel, DOCX, PDF, изображения.
"""

from pathlib import Path

from logger import logger
from tools._paths import clean_path, resolve_path
from tools.file_readers import (
    _CSV_EXTENSIONS,
    _DOCX_EXTENSIONS,
    _EXCEL_EXTENSIONS,
    _IMAGE_EXTENSIONS,
    _PDF_EXTENSIONS,
    _read_csv,
    _read_docx,
    _read_excel,
    _read_pdf,
    _safe_read,
)
from tools.models import ToolCall, ToolResult

_resolve = resolve_path

MAX_READ_FILES = 20

_READ_CACHE: dict[str, dict[str, dict]] = {}


def _current_session_id() -> str:
    try:
        from agent.loop import get_current_ctx
        ctx = get_current_ctx()
        if ctx and ctx.session_id:
            return ctx.session_id
    except Exception:
        pass
    return "_default"


def _session_cache() -> dict[str, dict]:
    sid = _current_session_id()
    bucket = _READ_CACHE.get(sid)
    if bucket is None:
        bucket = {}
        _READ_CACHE[sid] = bucket
    return bucket


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Объединяет пересекающиеся/соседние диапазоны строк (инклюзивно)."""
    if not ranges:
        return []
    sorted_r = sorted(ranges)
    merged: list[tuple[int, int]] = [sorted_r[0]]
    for s, e in sorted_r[1:]:
        ls, le = merged[-1]
        if s <= le + 1:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged


def _range_covered(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    """True, если [start, end] полностью покрыт уже виденными диапазонами."""
    return any(s <= start and end <= e for s, e in ranges)


def _format_seen_ranges(ranges: list[tuple[int, int]]) -> str:
    return ", ".join(f"{s}-{e}" if s != e else str(s) for s, e in ranges)


def _cache_get_valid(path: Path, key: str) -> dict | None:
    """Возвращает запись кэша текущей сессии, если файл не менялся."""
    bucket = _session_cache()
    entry = bucket.get(key)
    if entry is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        bucket.pop(key, None)
        return None
    if entry["mtime_ns"] != stat.st_mtime_ns or entry["size"] != stat.st_size:
        logger.debug("read cache: invalidated {} (mtime/size changed)", key)
        bucket.pop(key, None)
        return None
    return entry


def _cache_record(path: Path, key: str, start: int, end: int, *, binary: bool = False) -> dict:
    """Фиксирует факт чтения диапазона [start,end] в кэше текущей сессии."""
    try:
        stat = path.stat()
    except OSError:
        return {}
    bucket = _session_cache()
    entry = bucket.get(key)
    if entry is None or entry["mtime_ns"] != stat.st_mtime_ns or entry["size"] != stat.st_size:
        entry = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "ranges": [],
            "binary": binary,
            "read_count": 0,
        }
    entry["binary"] = binary
    entry["ranges"] = _merge_ranges(entry["ranges"] + [(start, end)])
    entry["read_count"] += 1
    bucket[key] = entry
    return entry


def invalidate_read_cache(path: Path | str) -> None:
    """Удаляет запись из кэша ВСЕХ сессий (файл изменён на диске).

    Ключ кэша пишется как str(resolve_path(path).resolve()) — working_dir-aware
    через ContextVar. Строим ключ ТЕМ ЖЕ путём (а не Path(path).resolve(),
    который резолвит относительно cwd процесса), иначе при cwd != working_dir
    ключи расходятся и инвалидация молча промахивается.
    """
    keys: set[str] = set()
    try:
        keys.add(str(resolve_path(str(path)).resolve()))
    except Exception:
        pass
    try:
        keys.add(str(Path(path).resolve()))
    except Exception:
        pass
    keys.add(str(path))
    for bucket in _READ_CACHE.values():
        for key in keys:
            bucket.pop(key, None)


def clear_read_cache(session_id: str | None = None) -> int:
    """Очищает кэш чтений.

    - session_id=None → очищает ТЕКУЩУЮ сессию (по контексту агента).
    - session_id="*"  → очищает ВСЕ сессии.
    - session_id=<id> → очищает указанную сессию.

    Возвращает число удалённых записей."""
    if session_id == "*":
        n = sum(len(b) for b in _READ_CACHE.values())
        _READ_CACHE.clear()
        logger.info("read cache: cleared ALL sessions, {} entries", n)
        return n
    sid = session_id if session_id else _current_session_id()
    bucket = _READ_CACHE.pop(sid, None)
    n = len(bucket) if bucket else 0
    logger.info("read cache: cleared session={} entries={}", sid[:16] if sid else "?", n)
    return n


def _parse_lines_range(lines_range: str, total_lines: int) -> tuple[int, int] | str | None:
    """Единый парсер диапазона строк — источник истины и для cache-coverage, и для вывода.

    Принимает 'A-B'/'A:B', 'A', открытые 'A-' (до конца файла) и '-B' (с начала).
    Возвращает:
      - (start, end) — валидный инклюзивный диапазон, клампленный по total_lines;
      - str          — текст ошибки (инвертированный/невалидный диапазон);
      - None         — пустая строка (фильтр не запрошен).
    Никогда не «проваливается» в полное чтение молча: на ошибку отдаём str.
    """
    raw = (lines_range or "").strip()
    norm = raw
    if norm.startswith("[") and norm.endswith("]"):
        norm = norm[1:-1].strip()
    norm = norm.replace(":", "-").replace(",", "-")
    if not norm:
        return None
    if "-" in norm:
        start_s, end_s = norm.split("-", 1)
        start_s, end_s = start_s.strip(), end_s.strip()
        try:
            start = max(1, int(start_s)) if start_s else 1
            # Открытый конец 'A-' → до конца файла.
            end = int(end_s) if end_s else total_lines
        except ValueError:
            return f"Invalid line range {raw!r}: expected 'A', 'A-B', 'A-' or '-B' with integers."
    else:
        try:
            start = max(1, int(norm))
        except ValueError:
            return f"Invalid line range {raw!r}: expected 'A', 'A-B', 'A-' or '-B' with integers."
        end = start
    if start > end:
        return f"Inverted line range {raw!r}: start {start} > end {end}."
    end = min(end, total_lines) if total_lines > 0 else end
    if start > end:
        # start за пределами файла после клампа конца — пустой диапазон.
        return f"Line range {raw!r} out of bounds: file has {total_lines} lines."
    return start, end


def _read_binary_cached(
    path: Path,
    cache_key: str,
    path_str: str,
    fmt: str,
    reader_fn,
    empty_msg: str,
    lines_range: str,
    command: str,
) -> ToolResult:
    """Общий код для бинарных форматов (CSV/Excel/PDF/DOCX): cache-hit → читатель → record → lines filter."""
    try:
        content = reader_fn(path)
        if not content:
            content = empty_msg
        total_lines = len(content.splitlines())
        info = f"[{path_str} · {fmt} · {total_lines} lines]"
        _cache_record(path, cache_key, 1, 1, binary=True)
        filtered = _apply_lines_filter(content, lines_range, path_str)
        if filtered != content:
            return ToolResult(name="read_files", status="ok", output=filtered, exit_code=0, command=command)
        return ToolResult(name="read_files", status="ok", output=f"{info}\n{content}", exit_code=0, command=command)
    except Exception as e:
        return ToolResult(
            name="read_files", status="error",
            output=f"Error reading {fmt}: {e}",
            exit_code=1, command=command,
        )


def _read_single_file(path_str: str, encoding: str = "utf-8", lines_range: str = "", command: str = "") -> ToolResult:
    """Читает один файл. Вызывается из read_files для каждого пути."""
    path = _resolve(path_str)

    if not path.exists():
        logger.debug("read_single_file: not found {}", path_str)
        return ToolResult(
            name="read_files",
            status="error",
            output=f"File not found: {path}",
            exit_code=1,
            command=command,
        )

    if path.is_dir():
        try:
            entries = sorted(path.iterdir(), key=lambda entry: entry.name.casefold())
        except OSError as e:
            return ToolResult(
                name="read_files", status="error", output=f"Read error: {e}",
                exit_code=1, command=command,
            )
        listing = "\n".join(
            f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries
        )
        return ToolResult(
            name="read_files", status="ok",
            output=f"[{path_str} · directory]\n{listing}",
            exit_code=0, command=command,
        )

    if not path.is_file():
        return ToolResult(
            name="read_files",
            status="error",
            output=f"Not a file: {path}",
            exit_code=1,
            command=command,
        )

    cache_key = str(path.resolve())

    if path.suffix.lower() in _IMAGE_EXTENSIONS:
        _cache_record(path, cache_key, 1, 1, binary=True)
        return ToolResult(
            name="read_files",
            status="ok",
            output=(
                f"[image: {path_str} · {path.suffix.lower()} — will be sent as image]"
            ),
            exit_code=0,
            command=command,
            image_path=path,
        )

    suffix = path.suffix.lower()
    if suffix in _CSV_EXTENSIONS:
        fmt = "tsv" if suffix == ".tsv" else "csv"
        return _read_binary_cached(
            path, cache_key, path_str, fmt,
            reader_fn=lambda p: _read_csv(p, encoding),
            empty_msg="(empty file)",
            lines_range=lines_range, command=command,
        )
    if suffix in _EXCEL_EXTENSIONS:
        return _read_binary_cached(
            path, cache_key, path_str, "excel",
            reader_fn=_read_excel,
            empty_msg="(empty workbook)",
            lines_range=lines_range, command=command,
        )
    if suffix in _PDF_EXTENSIONS:
        return _read_binary_cached(
            path, cache_key, path_str, "pdf",
            reader_fn=_read_pdf,
            empty_msg="(empty pdf)",
            lines_range=lines_range, command=command,
        )
    if suffix in _DOCX_EXTENSIONS:
        return _read_binary_cached(
            path, cache_key, path_str, "docx",
            reader_fn=_read_docx,
            empty_msg="(empty document)",
            lines_range=lines_range, command=command,
        )

    try:
        content = _safe_read(path, encoding)
    except Exception as e:
        return ToolResult(name="read_files", status="error", output=f"Read error: {e}", exit_code=1, command=command)

    MAX_LINES = 1000  # noqa: N806
    all_file_lines = content.splitlines()
    total_lines = len(all_file_lines)

    requested = _parse_lines_range(lines_range, total_lines) if lines_range else None
    if isinstance(requested, str):
        return ToolResult(
            name="read_files", status="error",
            output=f"[{path_str} · {total_lines} lines]\n{requested}",
            exit_code=1, command=command,
        )
    if not lines_range and total_lines > MAX_LINES:
        content_out = "\n".join(all_file_lines[:MAX_LINES])
        info = f"[{path_str} · {total_lines} lines (showing first {MAX_LINES})]"
        note = (
            f"\n\n⚠️ File truncated: showing {MAX_LINES} of {total_lines} lines. "
            f"Use {{\"path\": \"{path_str}\", \"lines\": \"{MAX_LINES + 1}-{total_lines}\"}} to read the rest."
        )
        _cache_record(path, cache_key, 1, MAX_LINES)
        return ToolResult(name="read_files", status="ok", output=f"{info}\n{content_out}{note}", exit_code=0, command=command, full_content=False)

    # ── Явный диапазон ── (через единый парсер, тот же splitlines-массив)
    if lines_range and requested is not None:
        start, end = requested
        selected = all_file_lines[start - 1 : end]
        header = f"[{path_str} lines {start}-{end} of {total_lines}]"
        body = "\n".join(
            f"{i}: {line}" for i, line in enumerate(selected, start=start)
        )
        _cache_record(path, cache_key, start, end)
        return ToolResult(name="read_files", status="ok", output=f"{header}\n{body}", exit_code=0, command=command, full_content=False)

    # ── Полное чтение без truncate ──
    info = f"[{path_str} · {total_lines} lines]"
    if total_lines > 0:
        _cache_record(path, cache_key, 1, total_lines)
    return ToolResult(name="read_files", status="ok", output=f"{info}\n{content}", exit_code=0, command=command, full_content=not lines_range)


def _apply_lines_filter(content: str, lines_range: str, path_str: str) -> str:
    """Apply a line-range filter to content via the single parser. Returns filtered, original, or an error string."""
    if not (lines_range or "").strip():
        return content
    all_lines = content.splitlines()
    parsed = _parse_lines_range(lines_range, len(all_lines))
    if parsed is None:
        return content
    if isinstance(parsed, str):
        return f"[{path_str} · {len(all_lines)} lines]\n{parsed}"
    start, end = parsed
    selected = all_lines[start - 1 : end]
    return f"[{path_str} lines {start}-{end} of {len(all_lines)}]\n" + "\n".join(
        f"{i}: {line}" for i, line in enumerate(selected, start=start)
    )


def read_files(call: ToolCall) -> ToolResult:
    """
    Читает один или несколько файлов (до 20).

    Принимает:
      {"paths": ["a.py", "b.py", ...]}             — несколько файлов
      {"paths": [{"path": "a.py", "lines": "1-10"}, {"path": "b.py"}]}  — с индивидуальными параметрами
    """
    args = call.args

    file_specs: list[dict] = []

    # path может быть list — трактуем как paths для совместимости.
    paths_arg = args.get("paths")
    if paths_arg is None and isinstance(args.get("path"), list):
        paths_arg = args.get("path")
    _dropped_paths: list[str] = []
    if paths_arg and isinstance(paths_arg, list):
        if len(paths_arg) > MAX_READ_FILES:
            for skipped in paths_arg[MAX_READ_FILES:]:
                if isinstance(skipped, str):
                    _dropped_paths.append(skipped)
                elif isinstance(skipped, dict):
                    _dropped_paths.append(str(skipped.get("path", "<unknown>")))
        for item in paths_arg[:MAX_READ_FILES]:
            if isinstance(item, str):
                file_specs.append({"path": item})
            elif isinstance(item, dict):
                file_specs.append(item)
    if not file_specs:
        return ToolResult(
            name="read_files",
            status="error",
            output="No files to read specified (path or paths)",
            exit_code=1,
            command=call.command,
        )

    results: list[ToolResult] = []
    first_image_path: Path | None = None

    for spec in file_specs:
        p = clean_path(spec.get("path", "") if isinstance(spec, dict) else str(spec))
        if not p:
            results.append(ToolResult(
                name="read_files", status="error",
                output="Empty path in paths", exit_code=1, command=call.command,
            ))
            continue
        lr = str(spec.get("lines", "")) if isinstance(spec, dict) else ""
        r = _read_single_file(p, encoding="utf-8", lines_range=lr, command=call.command)
        results.append(r)
        if r.image_path and not first_image_path:
            first_image_path = r.image_path

    if len(results) == 1:
        return results[0]

    parts: list[str] = []
    has_error = False
    for r in results:
        if r.status == "error":
            has_error = True
        parts.append(r.output)

    combined = "\n---\n".join(parts)
    status = "ok" if not has_error else "error"

    # Фикс 1.6: если paths был обрезан до MAX_READ_FILES — сообщаем модели,
    # что часть файлов НЕ прочитана, чтобы она могла попросить их следующим вызовом.
    if _dropped_paths:
        notice = (
            f"\n\n[NOTE] read_files принимает максимум {MAX_READ_FILES} файлов за вызов. "
            f"Не прочитано {len(_dropped_paths)}: "
            + ", ".join(_dropped_paths[:10])
            + (" …" if len(_dropped_paths) > 10 else "")
            + ". Повтори вызов с оставшимися путями."
        )
        combined = combined + notice

    all_full = all(r.full_content for r in results) and not _dropped_paths

    return ToolResult(
        name="read_files",
        status=status,
        output=combined,
        exit_code=0 if not has_error else 1,
        command=call.command,
        image_path=first_image_path,
        full_content=all_full,
    )
