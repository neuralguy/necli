"""Трекинг изменений файлов за шаг агента."""

import re
from dataclasses import dataclass, field

from logger import logger


@dataclass
class StepTracker:
    """Трекает изменения файлов за один шаг (сообщение) агента."""

    files_changed: set[str] = field(default_factory=set)
    lines_added: int = 0
    lines_removed: int = 0

    def record(self, tool_name: str, result_output: str, args: dict | None = None):
        """Записывает дельту по результату tool call."""
        if tool_name in ("create_file", "patch_file"):
            path = (args or {}).get("path", "")
            if path:
                self.files_changed.add(path)
            new_path = (args or {}).get("new_path", "") or (args or {}).get("dest", "")
            if new_path:
                self.files_changed.add(new_path)
            logger.debug(
                "StepTracker: {} touched={} files_total={}",
                tool_name,
                path or new_path,
                len(self.files_changed),
            )

        if tool_name == "patch_file":
            self._parse_patch_stats(result_output)

        elif tool_name == "create_file":
            self._parse_create_stats(result_output)

    def _parse_patch_stats(self, output: str):
        """Парсит summary patch_file: '✓ path updated (3 changed, +5 added, -2 removed)'."""
        m = re.search(r"\+(\d+)\s+added", output)
        if m:
            self.lines_added += int(m.group(1))
        m = re.search(r"-(\d+)\s+removed", output)
        if m:
            self.lines_removed += int(m.group(1))

    def _parse_create_stats(self, output: str):
        """Парсит вывод create_file: "✓ Created: path (N lines)".

        Для перезаписи ("✓ Overwritten: …") дельту строк точно не знаем, поэтому
        строки прибавляем только для созданного файла (файл всё равно отмечен
        изменённым через files_changed выше).
        """
        m = re.search(r"Created:.*\((\d+)\s+lines?\)", output)
        if m:
            self.lines_added += int(m.group(1))

    def reset(self):
        self.files_changed.clear()
        self.lines_added = 0
        self.lines_removed = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.files_changed) or self.lines_added > 0 or self.lines_removed > 0
