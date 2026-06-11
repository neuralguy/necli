"""Статистика проекта и трекинг изменений за шаг агента."""

import os
from pathlib import Path
from dataclasses import dataclass, field

from logger import logger
from config import is_ignored_dir


# Расширения, которые считаем «кодом» для подсчёта строк
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".java", ".kt", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".sh", ".bash", ".zsh", ".fish",
    ".css", ".scss", ".less", ".html", ".xml", ".svg",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sql", ".graphql", ".proto", ".md", ".rst", ".txt",
    ".lua", ".r", ".jl", ".ex", ".exs", ".erl", ".hs",
    ".swift", ".m", ".mm", ".cs", ".fs", ".scala",
    ".tf", ".hcl", ".nix", ".dhall",
    ".dockerfile", ".mk", ".cmake",
}

# IGNORE_DIRS — теперь канонический набор из config (через is_ignored_dir).


def count_project_stats(working_dir: str) -> tuple[int, int]:
    """Считает количество файлов и общее число строк в проекте.

    Returns:
        (file_count, total_lines)
    """
    root = Path(working_dir)
    if not root.is_dir():
        return 0, 0

    file_count = 0
    total_lines = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # Фильтруем игнорируемые директории in-place
        dirnames[:] = [d for d in dirnames if not is_ignored_dir(d)]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            suffix = fpath.suffix.lower()
            # Файлы без расширения, но с известным именем
            if suffix not in _CODE_EXTENSIONS:
                if fname.lower() not in ("makefile", "dockerfile", "rakefile", "gemfile", "procfile"):
                    continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                file_count += 1
                total_lines += lines
            except (OSError, PermissionError):
                continue

    return file_count, total_lines


@dataclass
class StepTracker:
    """Трекает изменения файлов за один шаг (сообщение) агента."""

    files_changed: set[str] = field(default_factory=set)
    lines_added: int = 0
    lines_removed: int = 0

    def record(self, tool_name: str, result_output: str, args: dict | None = None):
        """Записывает дельту по результату tool call."""
        if tool_name in ("write_file", "create_file", "patch_file", "delete_file",
                         "rename_file", "copy_file", "move_file"):
            path = (args or {}).get("path", "")
            if path:
                self.files_changed.add(path)
            new_path = (args or {}).get("new_path", "") or (args or {}).get("dest", "")
            if new_path:
                self.files_changed.add(new_path)
            logger.debug(
                "StepTracker: {} touched={} files_total={}",
                tool_name, path or new_path, len(self.files_changed),
            )

        # Парсим +/- строки из diff-вывода patch_file
        if tool_name == "patch_file":
            for line in result_output.split("\n"):
                stripped = line.strip()
                if stripped.startswith("+ ") and not stripped.startswith("+ ..."):
                    # Строка с номером: "+ 42  code"
                    parts = stripped[2:].split(None, 1)
                    if parts and parts[0].isdigit():
                        self.lines_added += 1
                elif stripped.startswith("- ") and not stripped.startswith("- ..."):
                    parts = stripped[2:].split(None, 1)
                    if parts and parts[0].isdigit():
                        self.lines_removed += 1

        elif tool_name == "write_file":
            self._parse_write_stats(result_output)

        elif tool_name == "create_file":
            self._parse_create_stats(result_output)

        elif tool_name == "delete_file":
            # Удалённый файл — все строки минус, но мы не знаем сколько было
            pass

    def _parse_write_stats(self, output: str):
        """Парсит вывод write_file для подсчёта строк."""
        # Формат: "✓ path: перезаписан (X → Y bytes), N строк"
        # или: "✓ path: создан (Y bytes), N строк"
        for line in output.split("\n"):
            if "строк" in line and "перезаписан" in line:
                # Перезапись — считаем как изменение, точную дельту не знаем
                pass
            elif "строк" in line and "создан" in line:
                # Новый файл через write_file
                try:
                    parts = line.split(",")
                    for p in parts:
                        p = p.strip()
                        if "строк" in p:
                            num = int(p.split()[0])
                            self.lines_added += num
                            break
                except (ValueError, IndexError):
                    pass

    def _parse_create_stats(self, output: str):
        """Парсит вывод create_file."""
        # Формат: "✓ Создан: path (N bytes, M строк)"
        for line in output.split("\n"):
            if "строк" in line:
                try:
                    # Ищем число перед "строк"
                    parts = line.split()
                    for i, word in enumerate(parts):
                        if word.startswith("строк") and i > 0:
                            num = int(parts[i - 1].rstrip(","))
                            self.lines_added += num
                            break
                except (ValueError, IndexError):
                    pass

    def reset(self):
        self.files_changed.clear()
        self.lines_added = 0
        self.lines_removed = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.files_changed) or self.lines_added > 0 or self.lines_removed > 0

    def format_step_stats(self) -> str:
        """Форматирует статистику шага: '2 files changed, +380 -15'"""
        if not self.has_changes:
            return ""
        parts = []
        n = len(self.files_changed)
        if n:
            parts.append(f"{n} file{'s' if n != 1 else ''} changed")
        delta_parts = []
        if self.lines_added:
            delta_parts.append(f"+{self.lines_added}")
        if self.lines_removed:
            delta_parts.append(f"-{self.lines_removed}")
        if delta_parts:
            parts.append(" ".join(delta_parts))
        return ", ".join(parts)


def format_project_stats(file_count: int, total_lines: int) -> str:
    """Форматирует: 'Project: 12 files, 6,340 lines'"""
    return f"Project: {file_count} files, {total_lines:,} lines"


def build_stats_line(working_dir: str, tracker: StepTracker) -> str:
    """Собирает полную строку статистики для subtitle.

    Формат: [Project: 12 files, 6,340 lines | This step: 2 files changed, +380 -15]
    """
    file_count, total_lines = count_project_stats(working_dir)
    parts = [format_project_stats(file_count, total_lines)]
    step = tracker.format_step_stats()
    if step:
        parts.append(f"This step: {step}")
    return " | ".join(parts)
