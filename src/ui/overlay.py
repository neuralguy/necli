"""Base contract for temporary terminal overlays."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .buffer_editing import edit_buffer_key

if TYPE_CHECKING:
    from .shell import Shell


class Overlay:
    """Виджет, временно забирающий нижнюю зону (между линиями рамки).

    Один за раз — никогда два одновременно. Подклассы описывают только то, как
    себя рисовать и как реагировать на клавиши; жизненным циклом и фокусом
    управляет Shell.
    """

    #: Нужен ли оверлею буфер свободного ввода (фильтр / поле текста).
    wants_text: bool = False
    #: Пустые ряды между scrollback и телом конкретного оверлея.
    top_margin_rows: int = 0
    #: Разрешить оверлею больше половины экрана (всю свободную высоту).
    expand_height: bool = False
    #: После закрытия вернуть компактный кадр ввода к нижнему краю терминала.
    restore_input_to_bottom: bool = False

    def __init__(self) -> None:
        self.shell: Shell | None = None
        self.future: asyncio.Future | None = None

    # ── то, что переопределяют подклассы ──
    def render(self, width: int) -> Any:
        """Rich-объект или готовая ANSI-строка для тела оверлея."""
        raise NotImplementedError

    def hint(self) -> str:
        """Строка подсказок под нижней линией рамки."""
        return ""

    def handle_key(self, key: str, event) -> bool:
        """True — клавиша обработана и дальше не идёт."""
        return False

    def on_text_changed(self, text: str) -> None:
        """Вызывается при изменении буфера ввода (если wants_text)."""

    def version(self) -> Any:
        """Метка состояния для кэша отрисовки.

        Пока метка не меняется, Shell считает, что `render()` вернёт то же
        самое, и НЕ дёргает Rich повторно — большая таблица (`/models`) иначе
        перерисовывалась бы на каждом кадре тикера. `None` означает «не знаю»:
        тогда кэш живёт ровно один кадр (это всё равно вдвое меньше рендеров,
        чем было, — раньше кадр считался дважды: лямбдой высоты и контролом).
        """
        return None

    # ── служебное ──
    def finish(self, result: Any) -> None:
        if self.future is not None and not self.future.done():
            self.future.set_result(result)

    @property
    def text(self) -> str:
        return self.shell.overlay_buffer.text if self.shell else ""

    @text.setter
    def text(self, value: str) -> None:
        if self.shell:
            self.shell.overlay_buffer.text = value

    def invalidate(self) -> None:
        if self.shell:
            self.shell.invalidate()

    def edit_text_key(self, key: str) -> bool:
        """Применить обычную клавишу редактора к тексту оверлея.

        Текстовые панели рисуют поле сами (в том числе маскируют API-ключи),
        поэтому prompt_toolkit не может отдать им стандартные биндинги
        ``BufferControl``. Один общий набор операций нужен и poll, и прочим
        ask_text-полям.
        """
        if self.shell is None:
            return False
        return edit_buffer_key(self.shell.overlay_buffer, key)
