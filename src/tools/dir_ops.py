"""Native directory operations — no shell subprocess needed."""

import fnmatch
import os
import re
import shutil
from pathlib import Path

import config

from tools.models import ToolCall, ToolResult
from tools._paths import resolve_path
from ui.formatting import format_size as _format_size

_BINARY_EXTENSIONS = frozenset({
    '.pyc', '.pyo', '.so', '.o', '.a',
    '.dll', '.exe', '.bin', '.dat',
    '.png', '.jpg', '.jpeg', '.gif',
    '.ico', '.webp', '.mp3', '.mp4', '.wav', '.flac', '.ogg',
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.mo', '.snap', '.lock',
})

# Дополнительные ignore только для grep — обычно содержат мусор для модели.
_GREP_IGNORE_DIRS = frozenset({
    "logs", ".git", "node_modules", ".next", ".nuxt",
    "target", "out", "coverage", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", ".tox", ".nox",
})
_GREP_IGNORE_FILES = frozenset({
    "uv.lock", "poetry.lock", "package-lock.json", "yarn.lock",
    "Cargo.lock", "Gemfile.lock", "pnpm-lock.yaml", "composer.lock",
})
_GREP_IGNORE_GLOBS = ("*.min.js", "*.min.css", "*.map", "*.bundle.js")
_GREP_MAX_LINE_LEN = 500       # строка длиннее — файл считаем минификатом
_GREP_MAX_PER_FILE = 30        # больше совпадений в одном файле сворачиваем

MAX_ENTRIES = 500
MAX_TREE_DEPTH = 8
MAX_TREE_ENTRIES = 1000
MAX_FIND_RESULTS = 200
MAX_GREP_RESULTS = 100

_resolve = resolve_path




def ls(call: ToolCall) -> ToolResult:
    """List directory contents.

    Args:
        path: directory to list (default: working dir)
        all: include hidden files
        long: detailed format with sizes (default: True)
    """
    args = call.args
    path_str = args.get("path", ".").strip()
    show_all = args.get("all", False)
    long_fmt = args.get("long", True)  # По умолчанию подробно

    path = _resolve(path_str)

    if not path.exists():
        return ToolResult(
            name="ls", status="error",
            output=f"Не найдена: {path}",
            exit_code=1, command=call.command,
        )

    if not path.is_dir():
        stat = path.stat()
        return ToolResult(
            name="ls", status="ok",
            output=(
                f"{path.name}  "
                f"{_format_size(stat.st_size)}  "
                f"file"
            ),
            exit_code=0, command=call.command,
        )

    try:
        entries = sorted(path.iterdir(), key=lambda e: (
            not e.is_dir(), e.name.lower(),
        ))
    except PermissionError:
        return ToolResult(
            name="ls", status="error",
            output=f"Нет доступа: {path}",
            exit_code=1, command=call.command,
        )

    if not show_all:
        entries = [e for e in entries if not e.name.startswith('.')]

    if len(entries) > MAX_ENTRIES:
        entries = entries[:MAX_ENTRIES]
        truncated = True
    else:
        truncated = False

    lines = []
    dir_count = 0
    file_count = 0
    total_size = 0

    for entry in entries:
        try:
            stat = entry.stat()
        except (PermissionError, OSError):
            lines.append(f"  ??  {entry.name}")
            continue

        if entry.is_dir():
            dir_count += 1
            if long_fmt:
                try:
                    child_count = sum(
                        1 for _ in entry.iterdir()
                    )
                except (PermissionError, OSError):
                    child_count = -1
                count_str = (
                    f"{child_count} items"
                    if child_count >= 0 else "?"
                )
                lines.append(
                    f"  📁 {entry.name}/  ({count_str})"
                )
            else:
                lines.append(f"  📁 {entry.name}/")
        else:
            file_count += 1
            total_size += stat.st_size
            if long_fmt:
                lines.append(
                    f"  📄 {entry.name}  "
                    f"{_format_size(stat.st_size)}"
                )
            else:
                lines.append(f"  📄 {entry.name}")

    header = f"[{path_str}] {dir_count} dirs, {file_count} files"
    if file_count and total_size:
        header += f", {_format_size(total_size)}"
    if truncated:
        header += f" (показано {MAX_ENTRIES}, есть ещё)"

    output = header + "\n" + "\n".join(lines)

    return ToolResult(
        name="ls", status="ok",
        output=output,
        exit_code=0, command=call.command,
    )


def tree(call: ToolCall) -> ToolResult:
    """Display directory tree.

    Args:
        path: directory to display (default: working dir)
        depth: maximum depth (default: 3, max: 8)
        all: include hidden files
    """
    args = call.args
    path_str = args.get("path", ".").strip()
    max_depth = min(int(args.get("depth", 3)), MAX_TREE_DEPTH)
    show_all = args.get("all", False)

    path = _resolve(path_str)

    if not path.exists():
        return ToolResult(
            name="tree", status="error",
            output=f"Не найдена: {path}",
            exit_code=1, command=call.command,
        )

    if not path.is_dir():
        return ToolResult(
            name="tree", status="error",
            output=f"Не директория: {path}",
            exit_code=1, command=call.command,
        )

    lines = [f"{path_str}/"]
    count = [0]
    _seen: set = set()

    def _walk(dir_path: Path, prefix: str, depth: int):
        if count[0] >= MAX_TREE_ENTRIES:
            return
        try:
            real = os.path.realpath(dir_path)
        except OSError:
            return
        if real in _seen:
            return
        _seen.add(real)

        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (
                not e.is_dir(), e.name.lower(),
            ))
        except (PermissionError, OSError):
            lines.append(f"{prefix}[нет доступа]")
            return

        if not show_all:
            entries = [
                e for e in entries
                if not e.name.startswith('.')
                and e.name not in config.IGNORE_DIRS
            ]

        for i, entry in enumerate(entries):
            if count[0] >= MAX_TREE_ENTRIES:
                lines.append(f"{prefix}... (лимит {MAX_TREE_ENTRIES})")
                return

            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                count[0] += 1
                if depth < max_depth:
                    _walk(entry, prefix + extension, depth + 1)
            else:
                size = ""
                try:
                    s = entry.stat().st_size
                    size = f"  ({_format_size(s)})"
                except (PermissionError, OSError):
                    pass
                lines.append(
                    f"{prefix}{connector}{entry.name}{size}"
                )
                count[0] += 1

    _walk(path, "", 1)

    return ToolResult(
        name="tree", status="ok",
        output='\n'.join(lines),
        exit_code=0, command=call.command,
    )


def mkdir(call: ToolCall) -> ToolResult:
    """Create a directory (including parents).

    Args:
        path: directory path to create
    """
    args = call.args
    path_str = args.get("path", "").strip()
    if not path_str:
        return ToolResult(
            name="mkdir", status="error",
            output="Не указан путь (path)",
            exit_code=1, command=call.command,
        )

    path = _resolve(path_str)

    if path.exists():
        if path.is_dir():
            return ToolResult(
                name="mkdir", status="ok",
                output=f"Уже существует: {path_str}",
                exit_code=0, command=call.command,
            )
        return ToolResult(
            name="mkdir", status="error",
            output=f"Путь занят файлом: {path_str}",
            exit_code=1, command=call.command,
        )

    try:
        path.mkdir(parents=True, exist_ok=True)
        return ToolResult(
            name="mkdir", status="ok",
            output=f"✓ Создана: {path_str}",
            exit_code=0, command=call.command,
        )
    except Exception as e:
        return ToolResult(
            name="mkdir", status="error",
            output=f"Ошибка: {e}",
            exit_code=1, command=call.command,
        )


def rmdir(call: ToolCall) -> ToolResult:
    """
    Удаляет директорию.

    Аргументы:
        path: str — путь
        force: bool (опц.) — удалить рекурсивно даже если не пустая
    """
    args = call.args
    path_str = args.get("path", "").strip()
    force = args.get("force", False)

    if not path_str:
        return ToolResult(
            name="rmdir", status="error",
            output="Не указан путь (path)",
            exit_code=1, command=call.command,
        )

    path = _resolve(path_str)

    if not path.exists():
        return ToolResult(
            name="rmdir", status="error",
            output=f"Не найдена: {path_str}",
            exit_code=1, command=call.command,
        )

    if not path.is_dir():
        return ToolResult(
            name="rmdir", status="error",
            output=(
                f"Не директория: {path_str} "
                f"(используйте delete_file)"
            ),
            exit_code=1, command=call.command,
        )

    resolved = str(path)
    dangerous = [
        os.path.expanduser("~"),
        "/", "/home", "/etc", "/usr", "/var",
        "/tmp", "/opt", "/bin", "/sbin",
        "/boot", "/dev", "/proc", "/sys", "/root",
        "/lib", "/lib64", "/lib32", "/libx32",
    ]
    if resolved in dangerous:
        return ToolResult(
            name="rmdir", status="error",
            output=f"Отказано: нельзя удалять {resolved}",
            exit_code=1, command=call.command,
        )

    try:
        if force:
            count = sum(1 for _ in path.rglob("*"))
            shutil.rmtree(path)
            return ToolResult(
                name="rmdir", status="ok",
                output=(
                    f"✓ Удалена рекурсивно: {path_str} "
                    f"({count} элементов)"
                ),
                exit_code=0, command=call.command,
            )
        else:
            try:
                path.rmdir()
            except OSError:
                children = list(path.iterdir())[:5]
                names = [c.name for c in children]
                return ToolResult(
                    name="rmdir", status="error",
                    output=(
                        f"Директория не пустая: {path_str} "
                        f"({', '.join(names)}...). "
                        f'Используйте "force": true'
                    ),
                    exit_code=1, command=call.command,
                )
            return ToolResult(
                name="rmdir", status="ok",
                output=f"✓ Удалена: {path_str}",
                exit_code=0, command=call.command,
            )
    except Exception as e:
        return ToolResult(
            name="rmdir", status="error",
            output=f"Ошибка: {e}",
            exit_code=1, command=call.command,
        )


def find_files(call: ToolCall) -> ToolResult:
    """
    Поиск файлов по имени/паттерну.

    Аргументы:
        path: str (опц.) — где искать (по умолч. рабочая директория)
        pattern: str — glob-паттерн (напр. "*.py", "test_*")
        name: str (альт.) — точное имя файла
        type: str (опц.) — "file", "dir", "any" (по умолч. "any")
        depth: int (опц.) — макс. глубина поиска
    """
    args = call.args
    path_str = args.get("path", ".").strip()
    pattern = (args.get("pattern") or args.get("query") or "").strip()
    name = args.get("name", "").strip()
    file_type = args.get("type", "any").strip()
    max_depth = int(args.get("depth", 99))

    path = _resolve(path_str)

    if not path.exists() or not path.is_dir():
        return ToolResult(
            name="find_files", status="error",
            output=f"Директория не найдена: {path_str}",
            exit_code=1, command=call.command,
        )

    if not pattern and not name:
        return ToolResult(
            name="find_files", status="error",
            output="Укажите pattern или name",
            exit_code=1, command=call.command,
        )

    results = []
    _seen_find: set = set()

    def _search(dir_path: Path, depth: int):
        if depth > max_depth or len(results) >= MAX_FIND_RESULTS:
            return
        try:
            real = os.path.realpath(dir_path)
        except OSError:
            return
        if real in _seen_find:
            return
        _seen_find.add(real)
        try:
            entries = sorted(dir_path.iterdir())
        except (PermissionError, OSError):
            return

        for entry in entries:
            if len(results) >= MAX_FIND_RESULTS:
                return

            if entry.name in config.IGNORE_DIRS:
                continue

            matches = False
            if name:
                matches = entry.name == name
            elif pattern:
                matches = fnmatch.fnmatch(entry.name, pattern)

            if matches:
                if file_type == "file" and not entry.is_file():
                    pass
                elif file_type == "dir" and not entry.is_dir():
                    pass
                else:
                    try:
                        rel = entry.relative_to(path)
                    except ValueError:
                        rel = entry
                    size = ""
                    if entry.is_file():
                        try:
                            size = f"  {_format_size(entry.stat().st_size)}"
                        except (PermissionError, OSError):
                            pass
                    kind = "📁" if entry.is_dir() else "📄"
                    results.append(f"  {kind} {rel}{size}")

            if entry.is_dir():
                _search(entry, depth + 1)

    _search(path, 1)

    if not results:
        search_term = pattern or name
        return ToolResult(
            name="find_files", status="ok",
            output=f"Ничего не найдено: {search_term} в {path_str}",
            exit_code=0, command=call.command,
        )

    header = (
        f"Найдено {len(results)} "
        f"в {path_str}"
    )
    if len(results) >= MAX_FIND_RESULTS:
        header += f" (лимит {MAX_FIND_RESULTS})"

    return ToolResult(
        name="find_files", status="ok",
        output=header + "\n" + '\n'.join(results),
        exit_code=0, command=call.command,
    )


def grep_files(call: ToolCall) -> ToolResult:
    """
    Поиск текста в файлах.

    Аргументы:
        pattern: str — regex или текст для поиска
        path: str (опц.) — где искать (по умолч. рабочая директория)
        glob: str (опц.) — фильтр файлов (напр. "*.py")
        ignore_case: bool (опц.) — без учёта регистра
        literal: bool (опц.) — искать буквально (не regex)
        context: int (опц.) — строк контекста до/после (по умолч. 0)
    """
    args = call.args

    def _as_str(v, name: str, default: str = "") -> str:
        if isinstance(v, list):
            raise ValueError(
                f"grep_files: '{name}' must be a string, got list "
                f"({len(v)} items). grep_files searches one path at a time — "
                f"call it separately for each path or use a glob pattern."
            )
        if v is None:
            return default
        return str(v).strip()

    try:
        search_pattern = _as_str(
            args.get("pattern") if args.get("pattern") is not None
            else args.get("query"),
            "pattern", "",
        )
        path_str = _as_str(args.get("path"), "path", ".") or "."
        file_glob = _as_str(args.get("glob"), "glob", "")
    except ValueError as e:
        return ToolResult(
            name="grep_files", status="error", output=str(e),
            exit_code=1, command=call.command,
        )
    ignore_case = args.get("ignore_case", False)
    literal = args.get("literal", False)
    context_lines = int(args.get("context", 0))
    include_ignored = bool(args.get("include_ignored", False))

    if not search_pattern:
        return ToolResult(
            name="grep_files", status="error",
            output="Укажите pattern для поиска",
            exit_code=1, command=call.command,
        )

    path = _resolve(path_str)

    if not path.exists():
        return ToolResult(
            name="grep_files", status="error",
            output=f"Не найден: {path_str}",
            exit_code=1, command=call.command,
        )

    flags = re.IGNORECASE if ignore_case else 0
    if literal:
        compiled = re.compile(re.escape(search_pattern), flags)
    else:
        try:
            compiled = re.compile(search_pattern, flags)
        except re.error as e:
            return ToolResult(
                name="grep_files", status="error",
                output=f"Неверный regex: {e}",
                exit_code=1, command=call.command,
            )
    results = []
    files_searched = 0
    files_matched = 0
    files_skipped_minified = 0
    files_truncated_count = 0

    def _is_skipped_by_name(name: str) -> bool:
        if include_ignored:
            return False
        if name in _GREP_IGNORE_FILES:
            return True
        for pat in _GREP_IGNORE_GLOBS:
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def _search_file(file_path: Path):
        nonlocal files_searched, files_matched, files_skipped_minified, files_truncated_count

        if file_path.suffix.lower() in _BINARY_EXTENSIONS:
            return
        if _is_skipped_by_name(file_path.name):
            return

        files_searched += 1

        # Фикс 6.7: пропускаем огромные файлы (>20MB) до read_text.
        # Иначе grep по 100MB-логу/dump'у сожрёт всю память.
        try:
            sz = file_path.stat().st_size
        except OSError:
            return
        if sz > 20 * 1024 * 1024:
            files_skipped_minified += 1
            return

        try:
            text = file_path.read_text(
                encoding="utf-8", errors="replace",
            )
        except (PermissionError, OSError):
            return

        lines = text.split('\n')
        # Эвристика: файл с очень длинными строками — минификат/сборка.
        # Проверяем сразу несколько срезов (начало/середина/конец), чтобы не
        # пропустить файлы вида "первые 50 нормальных + минификат в конце".
        if not include_ignored and lines:
            n = len(lines)
            mid = n // 2
            sample_slices = (lines[:50], lines[max(0, mid - 25): mid + 25], lines[-50:])
            max_line_len = 0
            for sl in sample_slices:
                for ln in sl:
                    if len(ln) > max_line_len:
                        max_line_len = len(ln)
                        if max_line_len > _GREP_MAX_LINE_LEN:
                            break
                if max_line_len > _GREP_MAX_LINE_LEN:
                    break
            if max_line_len > _GREP_MAX_LINE_LEN:
                files_skipped_minified += 1
                return

        file_matches = []
        for i, line in enumerate(lines):
            if compiled.search(line):
                file_matches.append((i + 1, line))

        if not file_matches:
            return

        files_matched += 1

        try:
            rel = file_path.relative_to(path)
        except ValueError:
            rel = file_path

        # Дедуп: если много матчей в одном файле — показываем первые N + сводку
        truncated_here = False
        shown_matches = file_matches
        if len(file_matches) > _GREP_MAX_PER_FILE:
            shown_matches = file_matches[:_GREP_MAX_PER_FILE]
            truncated_here = True
            files_truncated_count += 1

        for line_num, line in shown_matches:
            if len(results) >= MAX_GREP_RESULTS:
                return

            entry = f"  {rel}:{line_num}: {line.rstrip()}"
            if len(entry) > 200:
                entry = entry[:200] + "..."
            results.append(entry)

            if context_lines > 0:
                start = max(0, line_num - 1 - context_lines)
                end = min(len(lines), line_num + context_lines)
                for j in range(start, end):
                    if j == line_num - 1:
                        continue
                    ctx = f"    {j + 1}: {lines[j].rstrip()}"
                    if len(ctx) > 200:
                        ctx = ctx[:200] + "..."
                    results.append(ctx)

        if truncated_here and len(results) < MAX_GREP_RESULTS:
            extra = len(file_matches) - _GREP_MAX_PER_FILE
            # Plain text — Rich-разметка здесь не интерпретируется
            # (output идёт в Syntax/линейный рендер, не в Markdown).
            results.append(
                f"    ... +{extra} more matches in {rel} (raise grep limit if needed)"
            )

    _seen_grep: set = set()

    def _walk(dir_path: Path, depth: int = 0):
        if depth > MAX_TREE_DEPTH:
            return
        try:
            real = os.path.realpath(dir_path)
        except OSError:
            return
        if real in _seen_grep:
            return
        _seen_grep.add(real)
        try:
            entries = sorted(dir_path.iterdir())
        except (PermissionError, OSError):
            return

        for entry in entries:
            if len(results) >= MAX_GREP_RESULTS:
                return

            if entry.name in config.IGNORE_DIRS or entry.name == ".data":
                continue
            if not include_ignored and entry.is_dir() and entry.name in _GREP_IGNORE_DIRS:
                continue

            if entry.is_dir():
                _walk(entry, depth + 1)
            elif entry.is_file():
                if file_glob:
                    if not fnmatch.fnmatch(entry.name, file_glob):
                        continue
                _search_file(entry)

    if path.is_file():
        _search_file(path)
    else:
        _walk(path)

    if not results:
        hint = ""
        if not literal and re.search(r"[\\.\[\]()*+?^$|{}]", search_pattern):
            hint = " (if pattern contains regex special chars — try literal=true)"
        return ToolResult(
            name="grep_files", status="ok",
            output=(
                config.t("grep.no_matches", pattern=search_pattern,
                         path=path_str, checked=files_searched) + hint
            ),
            exit_code=0, command=call.command,
        )

    header = config.t("grep.found", n=len(results),
                      files=files_matched, checked=files_searched)
    if len(results) >= MAX_GREP_RESULTS:
        header += f" (limit {MAX_GREP_RESULTS})"
    if files_skipped_minified:
        header += f" · skipped minified: {files_skipped_minified}"
    if files_truncated_count:
        header += f" · compressed per file: {files_truncated_count}"

    return ToolResult(
        name="grep_files", status="ok",
        output=header + "\n" + '\n'.join(results),
        exit_code=0, command=call.command,
    )

