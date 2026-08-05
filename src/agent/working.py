"""Живой раундовый блок Working.

Один экземпляр охватывает весь пользовательский ход: все ответы модели и любое
количество вызванных ею инструментов. Внутренние API-запросы только обновляют
живой кадр; в scrollback он фиксируется один раз при завершении хода.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.console import Group
from rich.text import Text

from config.i18n import t as tr
from config.themes import t
from ui.formatting import format_tokens as _fmt_tokens
from ui.shell import get_shell

_WORKING_ZONE = "working"

_FINISH_WORKED = "worked"
_FINISH_STOPPED = "stopped"
_FINISH_INTERRUPTED = "interrupted"


def _finished_header(
    elapsed: float,
    outcome: str = _FINISH_WORKED,
    *,
    stopping: bool = False,
) -> Text:
    """Заголовок завершённого раунда для live и replay."""
    if outcome == _FINISH_INTERRUPTED:
        color = _darken(t("error"), 0.70) or t("error")
        icon, label = "■ ", "Interrupted"
    elif outcome == _FINISH_STOPPED:
        color = t("warning")
        icon, label = "■ ", "Stopping..." if stopping else "Stopped"
    else:
        color = t("success")
        icon, label = "✓ ", "Worked"

    header = Text()
    header.append(icon, style=f"bold {color}")
    header.append(label, style=f"bold {color}")
    header.append(" " + tr("working.seconds", n=round(elapsed)), style="dim")
    return header


def _usage_parts(usage: dict | None) -> tuple[int, int]:
    """Вернуть (input, output) из canonical и provider-specific usage."""
    usage = usage or {}
    def _first(*keys: str) -> int:
        for key in keys:
            try:
                value = int(usage.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value:
                return value
        return 0

    inp = _first("input_tokens", "input", "prompt_tokens")
    out = _first("output_tokens", "output", "completion_tokens")
    total = _first("total_tokens", "total")
    # Часть OpenAI-compatible прокси отдаёт total и только одну сторону.
    # Вторую можно восстановить без потери точности.
    if total:
        if not inp and out:
            inp = max(0, total - out)
        elif not out and inp:
            out = max(0, total - inp)
    return inp, out


def _darken(color: str, factor: float) -> str | None:
    """Затемнить hex-цвет, сохранив его оттенок; для иных форматов — None."""
    raw = str(color or "").removeprefix("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    if len(raw) != 6:
        return None
    try:
        channels = [int(raw[index:index + 2], 16) for index in (0, 2, 4)]
    except ValueError:
        return None
    factor = max(0.0, min(1.0, factor))
    shaded = [round(channel * factor) for channel in channels]
    return "#" + "".join(f"{channel:02x}" for channel in shaded)


@dataclass
class WorkingRound:
    ctx: object
    model: str = ""
    index: int = 1
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    token_estimate: int = 0
    estimated_output_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    current: str = ""
    declared_calls: int = 0
    calls: list[str] = field(default_factory=list)
    active_calls: list[str] = field(default_factory=list)
    active: bool = True
    outcome: str = _FINISH_WORKED

    def start(self) -> None:
        self.current = tr("working.waiting")
        shell = get_shell()
        if shell is not None and not getattr(self.ctx, "silent_console", False):
            shell.set_dynamic(_WORKING_ZONE, self.render)

    def begin_stream(self, model: str = "", index: int = 1) -> None:
        """Начать очередной внутренний ответ, не закрывая пользовательский ход."""
        if not self.active:
            return
        self.model = model or self.model
        self.index = index
        # set_usage() переносит оценку завершившегося стрима в общий счётчик.
        # Защитимся и от провайдера без usage: не теряем уже набежавшую оценку.
        if self.token_estimate:
            self.estimated_output_tokens += self.token_estimate
            self.token_estimate = 0
        self.current = tr("working.waiting")
        self.invalidate()

    @property
    def elapsed(self) -> float:
        return max(0.0, (self.finished_at or time.monotonic()) - self.started_at)

    @property
    def call_count(self) -> int:
        return max(self.declared_calls, len(self.calls))

    def update_stream(self, text: str, *, current_call: str = "") -> None:
        if not self.active:
            return
        if text:
            # На каждом SSE-чанке нужен O(1) апдейт. Полный tokenizer здесь
            # добавлял заметную паузу при первом кадре; точное usage всё равно
            # приходит финальным чанком провайдера.
            self.token_estimate = max(self.token_estimate, len(text) // 4)
        self.current = current_call or tr("working.responding")
        self.invalidate()

    def set_usage(self, usage: dict | None) -> None:
        inp, out = _usage_parts(usage)
        if inp or out:
            self.input_tokens += inp
            self.output_tokens += out
        else:
            self.estimated_output_tokens += self.token_estimate
        self.token_estimate = 0
        self.invalidate()

    def observe_calls(self, names: list[str]) -> None:
        clean = [str(name) for name in names if str(name) not in ("", "think", "plan")]
        self.declared_calls = max(self.declared_calls, len(clean))
        if clean and not self.calls:
            self.current = clean[0]
        self.invalidate()

    def begin_call(self, name: str, detail: str = "") -> None:
        if not self.active:
            return
        label = str(name or "tool")
        self.calls.append(label)
        active = label + (f" · {detail}" if detail else "")
        self.active_calls.append(active)
        self.current = active
        self.invalidate()

    def finish_call(self, name: str = "") -> None:
        if not self.active:
            return
        target = str(name or "")
        for index in range(len(self.active_calls) - 1, -1, -1):
            if not target or self.active_calls[index].split(" · ", 1)[0] == target:
                self.active_calls.pop(index)
                break
        self.current = self.active_calls[-1] if self.active_calls else tr("working.processing")
        self.invalidate()

    def invalidate(self) -> None:
        shell = get_shell()
        if shell is not None:
            shell.invalidate()

    def _shimmer(self) -> Text:
        label = "Working"
        result = Text()
        accent = t("accent")
        # Узкая тёмная волна проходит по акцентному слову. Цветовой тон не
        # меняется: меняется только яркость, полный цикл занимает ~3.6 с.
        cycle = len(label) + 6
        position = ((time.monotonic() - self.started_at) / 0.275) % cycle - 3
        for i, char in enumerate(label):
            strength = max(0.0, 1.0 - abs(i - position) / 2.1)
            shade = _darken(accent, 1.0 - 0.56 * strength)
            if shade is not None:
                result.append(char, style=f"bold {shade}")
            else:
                dim = " dim" if strength > 0.45 else ""
                result.append(char, style=f"bold{dim} {accent}")
        return result

    def _interrupt_outcome(self) -> str:
        """Текущее UI-состояние Ctrl+C, доступное ещё до finish()."""
        interrupt_level = int(getattr(self.ctx, "interrupt_level", 0) or 0)
        if getattr(self.ctx, "hard_interrupted", False) or interrupt_level >= 2:
            return _FINISH_INTERRUPTED
        if interrupt_level == 1:
            return _FINISH_STOPPED
        return _FINISH_WORKED

    def render(self, *, final: bool = False):
        if final:
            header = _finished_header(self.elapsed, self.outcome)
        else:
            live_outcome = self._interrupt_outcome()
            if live_outcome != _FINISH_WORKED:
                header = _finished_header(
                    self.elapsed,
                    live_outcome,
                    stopping=live_outcome == _FINISH_STOPPED,
                )
            else:
                header = Text()
                header.append_text(self._shimmer())
                header.append(" " + tr("working.seconds", n=round(self.elapsed)), style="dim")

        output_tokens = self.output_tokens + self.estimated_output_tokens + self.token_estimate
        output_prefix = "~" if self.estimated_output_tokens or self.token_estimate else ""
        details = Text("   ⎿  ", style=t("dim_text"))
        details.append(tr("working.calls", n=self.call_count), style=t("fg_primary"))
        details.append(" · ", style="dim")
        details.append(f"↑{_fmt_tokens(self.input_tokens)}", style=t("fg_primary"))
        details.append(" ", style="dim")
        details.append(
            f"↓{output_prefix}{_fmt_tokens(output_tokens)}", style=t("fg_primary"),
        )
        return Group(header, details)

    def finish(self, outcome: str | None = None) -> None:
        if not self.active:
            return
        self.active = False
        self.finished_at = time.monotonic()
        if outcome is None:
            outcome = self._interrupt_outcome()
        self.outcome = outcome
        if self.calls:
            self.current = self.calls[-1]
        elif self.current == tr("working.waiting"):
            self.current = tr("working.processing")
        shell = get_shell()
        if shell is not None and not getattr(self.ctx, "silent_console", False):
            shell.clear_dynamic(_WORKING_ZONE)
            shell.print_static(Group(Text(""), self.render(final=True)))
        try:
            store = getattr(self.ctx, "render_store", None)
            if store is not None:
                output_tokens = (
                    self.output_tokens + self.estimated_output_tokens + self.token_estimate
                )
                store.add("working", {
                    "elapsed": self.elapsed,
                    "calls": self.call_count,
                    "input_tokens": self.input_tokens,
                    "output_tokens": output_tokens,
                    "output_estimated": bool(
                        self.estimated_output_tokens or self.token_estimate
                    ),
                    # Старые версии replay по-прежнему читают общее поле tokens.
                    "tokens": self.input_tokens + output_tokens,
                    "current": self.current,
                    "outcome": self.outcome,
                })
        except Exception:
            pass


def begin_working_round(ctx, model: str = "", index: int = 1) -> WorkingRound:
    previous = getattr(ctx, "working_round", None)
    if previous is not None:
        previous.finish()
    current = WorkingRound(ctx=ctx, model=model or "", index=index)
    ctx.working_round = current
    current.start()
    return current


def continue_working_round(ctx, model: str = "", index: int = 1) -> WorkingRound:
    """Подключить очередной API-стрим к текущему пользовательскому ходу."""
    current = getattr(ctx, "working_round", None)
    if current is None or not current.active:
        current = begin_working_round(ctx, model, index)
    current.begin_stream(model, index)
    return current


def finish_working_round(
    ctx, *, force: bool = False, outcome: str | None = None,
) -> None:
    if ctx is None:
        return
    current = getattr(ctx, "working_round", None)
    if current is None:
        return
    if not force:
        try:
            from tools.background import has_running_work
            if has_running_work():
                return
        except Exception:
            pass
    current.finish(outcome=outcome)
    ctx.working_round = None


def current_working_round():
    try:
        from agent.loop import get_current_ctx
        ctx = get_current_ctx()
    except Exception:
        return None
    current = getattr(ctx, "working_round", None) if ctx is not None else None
    return current if current is not None and current.active else None
