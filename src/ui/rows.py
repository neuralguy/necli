"""Interactive rows shown below the terminal prompt."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .shell import Shell

logger = logging.getLogger(__name__)


class RowGroup:
    """Строка-индикатор под панелью ввода (например, группа субагентов).

    Появляется, пока группа жива; стрелкой вниз на неё встаёт фокус, Enter
    открывает связанный оверлей.
    """

    def __init__(
        self,
        label_fn: Callable[[], str],
        open_fn: Callable[[], None],
        *,
        kind: str = "agent",
        summary_count: int = 1,
        animated: bool = False,
    ) -> None:
        self._label_fn = label_fn
        self._open_fn = open_fn
        self.kind = kind
        self.summary_count = max(1, int(summary_count or 1))
        self.animated = bool(animated)
        self.shell: Shell | None = None

    def label(self) -> str:
        try:
            return self._label_fn()
        except Exception:
            logger.debug("RowGroup.label failed", exc_info=True)
            return "?"

    def open(self) -> None:
        try:
            self._open_fn()
        except Exception:
            logger.warning("RowGroup.open failed", exc_info=True)
