"""
Система планирования для агента.

Архитектура вдохновлена Claude Code Plan Mode и Cursor To-Do Lists.

Агент создаёт план перед выполнением задачи, разбивая её на шаги.
Каждый шаг имеет статус (pending/in_progress/done/skipped).
План обновляется по мере работы — агент всегда видит, где он.

План живёт в памяти сессии и инжектится в контекст каждого
сообщения, чтобы модель не теряла фокус.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from rich.console import Console
from rich.text import Text

logger = logging.getLogger(__name__)

console = Console()


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


_STATUS_ALIASES = {
    "pending": StepStatus.PENDING,
    "todo": StepStatus.PENDING,
    "open": StepStatus.PENDING,
    "new": StepStatus.PENDING,
    "in_progress": StepStatus.IN_PROGRESS,
    "in-progress": StepStatus.IN_PROGRESS,
    "in progress": StepStatus.IN_PROGRESS,
    "inprogress": StepStatus.IN_PROGRESS,
    "doing": StepStatus.IN_PROGRESS,
    "active": StepStatus.IN_PROGRESS,
    "working": StepStatus.IN_PROGRESS,
    "wip": StepStatus.IN_PROGRESS,
    "started": StepStatus.IN_PROGRESS,
    "done": StepStatus.DONE,
    "complete": StepStatus.DONE,
    "completed": StepStatus.DONE,
    "finished": StepStatus.DONE,
    "ok": StepStatus.DONE,
    "success": StepStatus.DONE,
    "succeeded": StepStatus.DONE,
    "closed": StepStatus.DONE,
    "skipped": StepStatus.SKIPPED,
    "skip": StepStatus.SKIPPED,
    "cancel": StepStatus.SKIPPED,
    "canceled": StepStatus.SKIPPED,
    "cancelled": StepStatus.SKIPPED,
    "n/a": StepStatus.SKIPPED,
    "na": StepStatus.SKIPPED,
}


def _normalize_status(s: str) -> Optional[StepStatus]:
    key = (s or "").strip().lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())  # collapse spaces
    key_us = key.replace(" ", "_")
    return _STATUS_ALIASES.get(key) or _STATUS_ALIASES.get(key_us)


@dataclass
class PlanStep:
    """Один шаг плана."""
    title: str
    status: StepStatus = StepStatus.PENDING
    notes: str = ""

    def _to_dict(self) -> dict:
        """Internal serialization for Plan._to_dict."""
        d = {"title": self.title, "status": self.status.value}
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def _from_dict(cls, d: dict) -> "PlanStep":
        """Internal deserialization for Plan._from_dict."""
        title = d.get("title") or d.get("step") or d.get("name") or d.get("text") or ""
        status_enum = _normalize_status(d.get("status", "pending")) or StepStatus.PENDING
        return cls(
            title=str(title),
            status=status_enum,
            notes=str(d.get("notes", "")),
        )


@dataclass
class Plan:
    """План выполнения задачи."""
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── Манипуляция шагами ──

    def set_steps(self, steps: list[dict]):
        """Установить шаги из списка словарей."""
        self.steps = []
        for s in steps:
            if isinstance(s, str):
                self.steps.append(PlanStep(title=s))
            elif isinstance(s, dict):
                self.steps.append(PlanStep._from_dict(s))
        self.updated_at = time.time()

    def update_step(
        self, index: int, status: Optional[str] = None,
        notes: Optional[str] = None,
    ):
        """Обновить статус/заметки шага по индексу."""
        if index not in range(len(self.steps)):
            return
        if status:
            normalized = _normalize_status(status)
            if normalized is not None:
                self.steps[index].status = normalized
            else:
                logger.warning("plan update: unknown status %r — ignored", status)
        if notes is not None:
            self.steps[index].notes = notes
        self.updated_at = time.time()

    def add_step(self, title: str, index: Optional[int] = None):
        """Добавить новый шаг (в конец или по индексу)."""
        step = PlanStep(title=title)
        if index is not None and index in range(len(self.steps) + 1):
            self.steps.insert(index, step)
        else:
            self.steps.append(step)
        self.updated_at = time.time()

    def remove_step(self, index: int):
        """Удалить шаг по индексу."""
        if index in range(len(self.steps)):
            self.steps.pop(index)
            self.updated_at = time.time()

    # ── Статистика ──

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def done_count(self) -> int:
        return sum(
            1 for s in self.steps
            if s.status in (StepStatus.DONE, StepStatus.SKIPPED)
        )

    @property
    def current_step(self) -> Optional[PlanStep]:
        """Первый шаг в статусе in_progress, или первый pending."""
        for s in self.steps:
            if s.status == StepStatus.IN_PROGRESS:
                return s
        for s in self.steps:
            if s.status == StepStatus.PENDING:
                return s
        return None

    @property
    def current_step_index(self) -> Optional[int]:
        for i, s in enumerate(self.steps):
            if s.status == StepStatus.IN_PROGRESS:
                return i
        for i, s in enumerate(self.steps):
            if s.status == StepStatus.PENDING:
                return i
        return None

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and all(
            s.status in (StepStatus.DONE, StepStatus.SKIPPED)
            for s in self.steps
        )

    @property
    def progress_str(self) -> str:
        if self.total == 0:
            return "0/0"
        return f"{self.done_count}/{self.total}"

    # ── Прогресс-бар ──

    @property
    def progress_bar(self) -> str:
        """Визуальный прогресс-бар: [████░░░░] 3/7"""
        if self.total == 0:
            return ""
        filled = self.done_count
        total = self.total
        bar_width = min(total, 20)
        filled_width = int(bar_width * filled / total)
        empty_width = bar_width - filled_width
        return "▮" * filled_width + "▯" * empty_width


    def render_for_context(self) -> str:
        """Рендерит план в текст для инжекции в контекст LLM.
        
        Показывает только окно: предыдущий шаг, текущий, следующий.
        """
        if not self.steps:
            return ""

        status_icons = {
            StepStatus.PENDING: "○",
            StepStatus.IN_PROGRESS: "▶",
            StepStatus.DONE: "✓",
            StepStatus.SKIPPED: "–",
        }

        # Определяем текущий шаг
        current_idx = self.current_step_index

        # Если нет текущего — план завершён или пуст
        if current_idx is None:
            if self.is_complete:
                return f"Plan [{self.progress_str}] — complete."
            return ""

        # Окно: prev, current, next
        window_start = max(0, current_idx - 1)
        window_end = min(len(self.steps), current_idx + 2)  # exclusive

        lines = [f"Plan [{self.progress_str}]"]

        for i in range(window_start, window_end):
            step = self.steps[i]
            icon = status_icons[step.status]
            line = f"  {i + 1}. [{icon}] {step.title}"
            if step.notes:
                line += f" — {step.notes}"
            lines.append(line)

        return "\n".join(lines)

# Двоеточий допускаем 2-3 (`::call`/`:::call`, `call::`/`call:::`) — модель часто
# роняет одно. Открытие якорим к началу строки, чтобы два двоеточия не задели
# мид-строчный код. Согласовано с tools/call_parser._OPEN_MARKER/_CLOSE_MARKER.
_PLAN_BLOCK_RE = re.compile(
    r'^[ \t]*:{2,3}call[ \t]+plan[^\n]*\n'
    r'(?P<body>.*?)'
    r'(?:\n|^)call:{2,3}[ \t]*(?:\n|$)',
    re.DOTALL | re.MULTILINE,
)
_PLAN_STRIP_RE = re.compile(
    r'^[ \t]*:{2,3}call[ \t]+plan[^\n]*\n'
    r'.*?'
    r'(?:\n|^)call:{2,3}[ \t]*(?:\n|$)',
    re.DOTALL | re.MULTILINE,
)
# Литеральная строка прогресса плана, которую модель эхо-повторяет из
# инжектированного в контекст render_for_context() (`Plan [N/M]` /
# `Plan [N/M] — complete.`). Срезаем из отображаемого текста, иначе
# последнее сообщение агента дублирует строку плана.
_PLAN_PROGRESS_LINE_RE = re.compile(
    r'(?m)^[ \t]*Plan \[\d+/\d+\](?:[ \t]+—[ \t]+complete\.)?[ \t]*$\n?',
)


@dataclass
class PlanCommand:
    """Распарсенная команда планирования."""
    action: str  # create | update | add_step | remove_step
    data: dict
    raw: str = ""


def _parse_plan_body(match):
    """Parse JSON from a :::call plan ... call::: block body."""
    body = match.group('body').strip()
    if not body:
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        try:
            fixed = body.replace(chr(39), chr(34))
            fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
            data = json.loads(fixed)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    action = data.get("action", "")
    valid = ("create", "update", "add_step", "remove_step")
    if action not in valid:
        return None
    
    # Validate required fields based on action
    if action == "create":
        if "steps" not in data:
            return None
        steps = data.get("steps")
        if not isinstance(steps, list) or len(steps) < 3:
            from logger import logger
            logger.warning(
                "plan create rejected: steps must be a list of 3+ items, got {} (raw_preview={!r})",
                len(steps) if isinstance(steps, list) else type(steps).__name__,
                match.group(0)[:200],
            )
            return None
        # 'goal' field is optional, defaults to empty string

    return PlanCommand(action=action, data=data, raw=match.group(0))


def parse_plan_commands(text: str) -> list[PlanCommand]:
    """Извлекает команды плана из :::call plan ... call::: блоков ответа модели."""
    commands = []
    for match in _PLAN_BLOCK_RE.finditer(text):
        cmd = _parse_plan_body(match)
        if cmd:
            commands.append(cmd)
    return commands


def strip_plan_commands(text: str) -> str:
    """Убирает :::call plan ... call::: блоки из текста."""
    if not text:
        return ""
    result = _PLAN_STRIP_RE.sub('', text)
    result = _PLAN_PROGRESS_LINE_RE.sub('', result)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _first_present(data: dict, *keys):
    """Возвращает значение первого присутствующего ключа.

    В отличие от цепочки ``or``, корректно обрабатывает falsy-значения
    (например, 0-based индекс ``0``) — учитывается само наличие ключа и
    то, что значение не ``None``.
    """
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _resolve_step_index(plan: Plan, data: dict) -> Optional[int]:
    """Разрешает индекс шага из update-команды.

    Принимает поля: step, index, step_index, step_number, n, id, step_id, title.
    Если ни одного нет — целится в current_step (первый in_progress/pending).
    Возвращает 0-based индекс или None.
    """
    if not plan.steps:
        return None

    raw = _first_present(data, "step", "index", "step_index",
                          "step_number", "n", "id", "step_id")
    if raw is not None:
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            idx = None
        if idx is not None:
            if 1 <= idx <= len(plan.steps):
                return idx - 1
            if 0 <= idx < len(plan.steps):
                return idx
            sid = str(raw)
            for i, st in enumerate(plan.steps):
                if str(getattr(st, "id", "")) == sid:
                    return i
    needle = data.get("title") or data.get("name") or data.get("text")
    if isinstance(needle, str) and needle.strip():
        nl = needle.strip().lower()
        for i, st in enumerate(plan.steps):
            if nl in (st.title or "").lower():
                return i
    # Нет ничего — берём current_step (in_progress > pending).
    cur_idx = plan.current_step_index
    if cur_idx is not None:
        return cur_idx
    return None


def resolve_plan_command_focus(plan: Optional[Plan], cmd: PlanCommand) -> Optional[int]:
    if cmd.action == "create":
        return None
    if plan is None:
        return None
    if cmd.action == "update":
        return _resolve_step_index(plan, cmd.data)
    if cmd.action == "add_step":
        index = cmd.data.get("index")
        if index is not None:
            try:
                idx = int(index)
            except (TypeError, ValueError):
                return len(plan.steps)
            if 1 <= idx <= len(plan.steps) + 1:
                return idx - 1
            if 0 <= idx <= len(plan.steps):
                return idx
        return len(plan.steps)
    if cmd.action == "remove_step":
        return _resolve_remove_index(plan, cmd.data)
    return None


def _resolve_remove_index(plan: Plan, data: dict) -> Optional[int]:
    """0-based индекс шага для удаления (поддерживает step/index, 1- и 0-based)."""
    raw = _first_present(data, "step", "index", "step_index", "step_number", "n")
    if raw is None:
        return None
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= idx <= len(plan.steps):
        return idx - 1
    if 0 <= idx < len(plan.steps):
        return idx
    return None


def plan_to_snapshot(plan: Plan) -> dict:
    return {
        "goal": plan.goal,
        "steps": [step._to_dict() for step in plan.steps],
    }


def apply_plan_commands(
    plan: Optional[Plan],
    commands: list[PlanCommand],
) -> Plan:
    """
    Применяет список PlanCommand к текущему плану.
    Возвращает обновлённый (или новый) план.
    """
    for cmd in commands:
        if cmd.action == "create":
            goal = cmd.data.get("goal", "")
            steps = cmd.data.get("steps", [])
            plan = Plan(goal=goal)
            plan.set_steps(steps)

        elif plan is not None:
            if cmd.action == "update":
                step_idx = _resolve_step_index(plan, cmd.data)
                if step_idx is not None:
                    plan.update_step(
                        step_idx,
                        status=cmd.data.get("status"),
                        notes=cmd.data.get("notes"),
                    )
                else:
                    logger.warning(
                        "plan update: step not resolved, data=%r, steps=%d",
                        cmd.data, len(plan.steps),
                    )

            elif cmd.action == "add_step":
                title = cmd.data.get("title", "")
                index = cmd.data.get("index")
                insert_index = None
                if index is not None:
                    try:
                        idx = int(index)
                    except (TypeError, ValueError):
                        idx = None
                    if idx is not None:
                        if 1 <= idx <= len(plan.steps) + 1:
                            insert_index = idx - 1
                        elif 0 <= idx <= len(plan.steps):
                            insert_index = idx
                if title:
                    plan.add_step(title, index=insert_index)

            elif cmd.action == "remove_step":
                step_idx = _resolve_remove_index(plan, cmd.data)
                if step_idx is not None:
                    plan.remove_step(step_idx)

    return plan


_STATUS_STYLES = {
    StepStatus.PENDING: ("○", "dim"),
    StepStatus.IN_PROGRESS: ("▶", "bold cyan"),
    StepStatus.DONE: ("✓", "green"),
    StepStatus.SKIPPED: ("–", "dim yellow"),
}


def render_plan_panel(
    plan: Plan,
    compact: bool = False,
    *,
    focus_index: Optional[int] = None,
    full: bool = True,
):
    """
    Рендерит план как Rich Panel.

    compact=True — для встраивания в Live-стрим (без лишних отступов).
    compact=False — для статичного вывода.
    full=False + focus_index — показывает окно: прошлый, изменённый, следующий.
    Если глобальный compact_mode включён — возвращает Group без рамки.
    """
    lines = Text()

    indices = list(range(len(plan.steps)))
    if not full and plan.steps:
        if focus_index is None:
            focus_index = plan.current_step_index
        if focus_index is None:
            focus_index = 0
        focus_index = max(0, min(int(focus_index), len(plan.steps) - 1))
        indices = list(range(max(0, focus_index - 1), min(len(plan.steps), focus_index + 2)))

    last_visible_idx = indices[-1] if indices else -1

    for i in indices:
        step = plan.steps[i]
        icon, style = _STATUS_STYLES[step.status]

        # Номер шага
        num_style = style if step.status != StepStatus.PENDING else "dim"
        lines.append("   ")
        lines.append(f"{i + 1}. ", style=num_style)

        # Иконка
        lines.append(f"{icon} ", style=style)

        # Текст шага
        if step.status == StepStatus.IN_PROGRESS:
            lines.append(f"{step.title}", style="bold cyan")
        elif step.status == StepStatus.DONE:
            lines.append(f"{step.title}", style="green")
        elif step.status == StepStatus.SKIPPED:
            lines.append(f"{step.title}", style="dim yellow strikethrough")
        else:
            lines.append(f"{step.title}", style="dim")

        # Заметки
        if step.notes:
            lines.append(f"  ({step.notes})", style="dim italic")

        if i != last_visible_idx:
            lines.append("\n")

    # Заголовок панели
    title = f"📋 Plan [{plan.progress_str}]"
    if plan.is_complete:
        title += " ✓"
        border_style = "green"
    elif plan.current_step:
        border_style = "cyan"
    else:
        border_style = "dim cyan"

    # Подзаголовок — цель
    subtitle = _truncate_goal(plan.goal)

    from rich.console import Group as RGroup
    header = Text()
    header.append(title, style=f"bold {border_style}")
    if subtitle:
        header.append(f"  {subtitle}", style="dim")
    header.append("  Ctrl+O", style="dim")
    return RGroup(header, lines)


def _truncate_goal(goal: str, max_len: int = 60) -> str:
    goal = goal.replace("\n", " ").strip()
    if len(goal) > max_len:
        return goal[:max_len - 1] + "…"
    return goal


PLAN_FILENAME = ".plan.md"


def _render_plan_markdown(plan: Plan) -> str:
    status_icons = {
        StepStatus.PENDING: "⏳",
        StepStatus.IN_PROGRESS: "🔄",
        StepStatus.DONE: "✅",
        StepStatus.SKIPPED: "⏭️",
    }
    lines = [f"# Plan: {plan.goal}", ""]
    lines.append(f"Progress: {plan.done_count}/{plan.total}")
    lines.append("")
    for i, step in enumerate(plan.steps):
        icon = status_icons[step.status]
        line = f"{i + 1}. {icon} {step.title}"
        if step.notes:
            line += f" — {step.notes}"
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def _plan_path(plan_dir: str) -> str:
    return os.path.join(plan_dir, PLAN_FILENAME)


def save_plan_file(plan: Plan, plan_dir: str) -> None:
    try:
        os.makedirs(plan_dir, exist_ok=True)
    except OSError as e:
        logger.warning("Failed to create plan dir %s: %s", plan_dir, e)
        return
    path = _plan_path(plan_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_render_plan_markdown(plan))
    except OSError as e:
        logger.warning("Failed to save %s: %s", path, e)


def load_plan_file(plan_dir: str) -> Optional[Plan]:
    path = _plan_path(plan_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None
    return _parse_plan_markdown(content)


def _parse_plan_markdown(text: str) -> Optional[Plan]:
    lines = text.strip().splitlines()
    if not lines:
        return None

    goal = ""
    for line in lines:
        if line.startswith("# Plan: "):
            goal = line[len("# Plan: "):].strip()
            break
    if not goal:
        return None

    icon_to_status = {
        "⏳": StepStatus.PENDING,
        "🔄": StepStatus.IN_PROGRESS,
        "✅": StepStatus.DONE,
        "⏭️": StepStatus.SKIPPED,
        "⏭": StepStatus.SKIPPED,
    }

    step_re = re.compile(r"^(\d+)\.\s+(\S+)\s+(.+)$")
    steps = []
    for line in lines:
        m = step_re.match(line)
        if not m:
            continue
        icon = m.group(2)
        rest = m.group(3)
        status = icon_to_status.get(icon, StepStatus.PENDING)
        notes = ""
        if " — " in rest:
            title, notes = rest.split(" — ", 1)
        else:
            title = rest
        steps.append(PlanStep(title=title.strip(), status=status, notes=notes.strip()))

    if not steps:
        return None

    plan = Plan(goal=goal)
    plan.steps = steps
    return plan


def delete_plan_file(plan_dir: str) -> None:
    path = _plan_path(plan_dir)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Failed to delete %s: %s", path, e)

