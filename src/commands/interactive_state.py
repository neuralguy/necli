from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape

from config.themes import t
from session import Session, storage

console = Console()


@dataclass
class InteractiveState:
    """Изменяемое состояние интерактивного цикла."""

    session: Session
    msg_num: int = 0
    cur_model: str = ""
    last_elapsed: float | None = None
    pending_context: list[dict] | None = None

    workdir: str = ""
    prompt_input: object = None  # ui.prompt.InputPrompt
    current_ctx: object = (
        None  # agent.loop.AgentContext (для Ctrl+O из потока run_in_terminal)
    )

    mode_state: dict = field(
        default_factory=lambda: {"mode": "agent", "changed": False}
    )
    think_enabled: bool = False
    think_changed: bool = False
    activity_status: str = "idle"
    history_cleared_at: float = (
        0.0  # timestamp последнего /new для scrollback/replay marker
    )

    recap_task: object = None  # asyncio.Task генерации рекапа текущего раунда
    recap_background_tasks: set[object] = field(default_factory=set)
    memory_background_tasks: set[object] = field(
        default_factory=set
    )  # периодическая чистка памяти
    memory_cleanup_started: bool = False
    memory_cleanup_active: bool = False

    def save_session(self) -> None:
        try:
            storage.save(self.session)
        except Exception as e:
            console.print(
                f"  [{t('warning')}]⚠[/{t('warning')}] [dim]Save error: {escape(str(e))}[/dim]"
            )
