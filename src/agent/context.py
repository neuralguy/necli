"""Контекст агентной сессии — замена глобального состояния."""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from agent.project_stats import StepTracker
from agent.render_store import RenderStore
from planner import Plan

if TYPE_CHECKING:
    from agent.events import AgentEventHandler


@dataclass
class AgentContext:
    plan: Plan | None = None
    working_dir: str = field(default_factory=os.getcwd)
    plan_dir: str = ""
    event_handler: Optional["AgentEventHandler"] = None
    interrupted: bool = False
    hard_interrupted: bool = False
    interrupt_level: int = 0
    mode: str = "agent"
    session_id: str = ""
    step_tracker: StepTracker = field(default_factory=StepTracker)
    last_fs_snapshot: dict | None = None
    silent_console: bool = False
    render_store: RenderStore = field(default_factory=RenderStore)
    turn_start_time: float = field(default_factory=time.monotonic)
    working_round: object | None = None
    prompt_input: object | None = None
    #: Вызывается после каждого действия агента (инструмент, ответ, субагент),
    #: чтобы статус-панель над вводом показывала свежие usage и git. Ставится
    #: интерактивным циклом; вне интерактива (headless) остаётся None.
    refresh_status: Callable[[], None] | None = None

    @property
    def effective_plan_dir(self) -> str:
        return self.plan_dir or self.working_dir

    def reset_interrupt(self):
        self.interrupted = False
        self.hard_interrupted = False
        self.interrupt_level = 0

