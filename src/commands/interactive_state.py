from dataclasses import dataclass, field

from rich.console import Console
from rich.markup import escape

import session.storage as storage
from session import Session

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

    mode_state: dict = field(default_factory=lambda: {"mode": "agent", "changed": False})
    think_enabled: bool = False
    think_changed: bool = False
    activity_status: str = "idle"

    recap_task: object = None  # asyncio.Task с фоновым рекапом текущего раунда

    def save_session(self) -> None:
        try:
            storage.save(self.session)
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] [dim]Save error: {escape(str(e))}[/dim]")
