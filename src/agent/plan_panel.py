"""Живая панель плана в динамической зоне Shell.

Свёрнутый режим (дефолт) держит над блоком Working только заголовок плана,
текущий и следующий шаг; ``/plan`` разворачивает панель до полного списка
и сворачивает обратно. По завершении плана один раз печатается финальная
выполненная панель в статичный scrollback, зона гаснет.
"""

from __future__ import annotations

_PLAN_ZONE = "plan"


def _shell():
    """Shell для живой зоны; None, если TUI нет или ход идёт мимо него."""
    try:
        from agent.loop import get_current_ctx

        ctx = get_current_ctx()
    except Exception:
        ctx = None
    if ctx is not None and getattr(ctx, "silent_console", False):
        return None
    try:
        from ui.shell import get_shell

        return get_shell()
    except Exception:
        return None


class PlanPanel:
    """Состояние живой панели: текущий Plan и режим отображения."""

    def __init__(self) -> None:
        self._plan = None
        self._expanded = False
        self._final_printed = False

    @property
    def plan(self):
        return self._plan

    @property
    def has_live_plan(self) -> bool:
        return self._plan is not None and bool(self._plan.steps) and not self._plan.is_complete

    def update(self, plan, action: str = "", focus_index: int | None = None) -> bool:
        """Событие on_plan_update. True — потреблено живой панелью.

        Завершённый план в живой зоне не задерживается: один финальный
        статичный блок — и зона гаснет, чтобы «выполненный» план не висел
        над вводом вечно.
        """
        shell = _shell()
        if shell is None or plan is None or not getattr(plan, "steps", None):
            return False
        self._plan = plan
        if plan.is_complete:
            self._print_final(shell)
            return True
        self._final_printed = False
        shell.set_dynamic(_PLAN_ZONE, self.render)
        shell.invalidate()
        return True

    def _print_final(self, shell) -> None:
        if self._final_printed:
            return
        self._final_printed = True
        shell.clear_dynamic(_PLAN_ZONE)
        from agent.display import show_plan_update

        show_plan_update(self._plan)

    def toggle(self) -> str:
        """/plan: развернуть/свернуть панель. Возвращает статус для пользователя."""
        from config.i18n import t as tr

        if self._plan is None or not self._plan.steps:
            return tr("plan.none")
        if self._plan.is_complete:
            return tr("plan.completed")
        self._expanded = not self._expanded
        shell = _shell()
        if shell is not None:
            shell.set_dynamic(_PLAN_ZONE, self.render)
            shell.invalidate()
        return tr("plan.expanded" if self._expanded else "plan.collapsed")

    def reset(self) -> None:
        """Погасить зону: новая сессия/ветка — план прежнего хода неактуален."""
        self._plan = None
        self._expanded = False
        self._final_printed = False
        shell = _shell()
        if shell is not None:
            shell.clear_dynamic(_PLAN_ZONE)
            shell.invalidate()

    def render(self):
        plan = self._plan
        if plan is None or not plan.steps:
            return ""
        from planner import render_plan_panel

        if self._expanded:
            return render_plan_panel(plan, compact=True, full=True, hint="/plan")
        return render_plan_panel(
            plan,
            compact=True,
            full=False,
            focus_index=plan.current_step_index,
            collapsed=True,
            hint="/plan",
        )


_PANEL = PlanPanel()


def handle_plan_update(plan, action: str = "", focus_index: int | None = None) -> bool:
    """Мост RichEventHandler.on_plan_update → живая панель.

    False — панель недоступна (нет TUI или шагов у плана): событие уходит
    в прежний статичный рендер.
    """
    try:
        return _PANEL.update(plan, action=action, focus_index=focus_index)
    except Exception:
        from logger import logger

        logger.debug("plan panel update failed", exc_info=True)
        return False


def toggle_plan() -> str:
    """Смена режима панели по /plan."""
    return _PANEL.toggle()


def reset_plan_panel() -> None:
    """Сброс панели при смене сессии/ветки."""
    _PANEL.reset()
