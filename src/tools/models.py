"""Модели данных для инструментов."""

from dataclasses import dataclass, field
from pathlib import Path

# Аргумент инструмента, который показывается в заголовке панели/титуле вызова.
# Единый источник истины: display.py и call_parser.py импортируют отсюда.
TOOL_TITLE_ARG: dict[str, str] = {
    "web_fetch": "urls",
    "web_search": "queries",
    "skill": "name",
    "memory": "name",
    "poll": "question",
    "subagent": "prompt",
    "expand_tool_result": "id",
    "read": "path",
    "docx": "path",
    "pptx": "path",
}


@dataclass
class ToolCall:
    """Вызов инструмента, извлечённый из ответа модели."""

    command: str
    tool_name: str = "shell"
    args: dict = field(default_factory=dict)
    raw: str = ""

    def __repr__(self):
        preview = self.command[:80].replace("\n", "\\n")
        return f"ToolCall({self.tool_name}, command={preview!r})"

    @property
    def name(self) -> str:
        """Human-friendly name: tool_name for named tools, first command word for shell."""
        if self.tool_name != "shell":
            return self.tool_name
        first_line = self.command.strip().split("\n")[0]
        return first_line.split()[0] if first_line.split() else "shell"


@dataclass
class ToolResult:
    """Результат выполнения инструмента."""

    name: str
    status: str  # "ok" | "error"
    output: str
    exit_code: int = 0
    command: str = ""
    image_path: Path | None = None
    image_paths: list[Path] | None = None
    elapsed: float = 0.0
    full_content: bool = False
    fatal: bool = False
    # patch_file: 1-based стартовые строки применённых блоков в ИСХОДНОМ файле
    # (до правки). Нужно для корректной нумерации diff-превью — после записи
    # find_text в файле уже нет, искать его поздно.
    line_starts: list[int] | None = None
    # patch_file: actual line-level hunks produced by the successful edit.
    # Keeping the applied diff on the result makes UI/replay independent from
    # how the model expressed the patch (native JSON vs fenced FIND/REPLACE).
    patch_changes: list[dict] | None = None

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "status": self.status,
            "output": self.output,
            "exit_code": self.exit_code,
            "command": self.command,
        }
        if self.full_content:
            d["full_content"] = True
        if self.line_starts:
            d["line_starts"] = list(self.line_starts)
        if self.patch_changes:
            d["patch_changes"] = [dict(change) for change in self.patch_changes]
        return d
