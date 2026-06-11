"""Контекст агентной сессии — замена глобального состояния."""

import os
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from planner import Plan
from agent.project_stats import StepTracker
from agent.render_store import RenderStore

if TYPE_CHECKING:
    from agent.events import AgentEventHandler


@dataclass
class AgentContext:
    plan: Optional[Plan] = None
    working_dir: str = field(default_factory=os.getcwd)
    plan_dir: str = ""
    event_handler: Optional["AgentEventHandler"] = None
    original_message: str = ""
    interrupted: bool = False
    hard_interrupted: bool = False
    mode: str = "agent"
    site_id: str = ""
    page_mode: str = "chat"
    session_id: str = ""
    step_tracker: StepTracker = field(default_factory=StepTracker)
    last_fs_snapshot: Optional[dict] = None
    silent_console: bool = False
    render_store: RenderStore = field(default_factory=RenderStore)
    welcome_args: Optional[dict] = None
    turn_start_time: float = field(default_factory=time.monotonic)
    last_status_text: str = ""
    # Callback пересчёта status-строки из текущего state. Нужен на Ctrl+O
    # reprint: после compress/decompress last_status_text может устареть/опустеть,
    # тогда вместо голой линии пересчитываем актуальный статус.
    rebuild_status: Optional[object] = None
    prompt_input: Optional[object] = None

    @property
    def effective_plan_dir(self) -> str:
        return self.plan_dir or self.working_dir

    def reset_plan(self):
        self.plan = None

    def reset_interrupt(self):
        self.interrupted = False
        self.hard_interrupted = False

    def toggle_mode(self) -> str:
        """Циклит режимы agent ↔ planning."""
        self.mode = "planning" if self.mode == "agent" else "agent"
        return self.mode



