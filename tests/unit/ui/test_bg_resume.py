"""ui/prompt.py — прерывание ожидания ввода завершением фоновой задачи.

Проверяем ключевое правило: bg-резюм срабатывает только когда поле ввода
ПУСТО (не мешаем печатающему пользователю), и приоритет у реального ввода.
"""

import asyncio

import pytest

from tools import background as bg
from ui.prompt import _BG_RESUME, InputPrompt


class _FakeBuffer:
    def __init__(self, text=""):
        self.text = text


class _FakeApp:
    def __init__(self, text=""):
        self.current_buffer = _FakeBuffer(text)
        self.exited_with = None

    def exit(self, result=None):
        self.exited_with = result


class _FakeSession:
    """Подменяет PromptSession: prompt_async ждёт, пока его не «завершат»."""

    def __init__(self, buffer_text=""):
        self.app = _FakeApp(buffer_text)
        self._fut: asyncio.Future = asyncio.get_event_loop().create_future()

    async def prompt_async(self, *a, **k):
        # exit() резолвит future (как делает prompt_toolkit при app.exit()).
        return await self._fut

    def resolve(self, value):
        if not self._fut.done():
            self._fut.set_result(value)


def _make_prompt(buffer_text=""):
    p = InputPrompt.__new__(InputPrompt)  # без полного __init__ (он тянет PromptSession)
    p._session = _FakeSession(buffer_text)
    p._make_prompt_fragments = lambda: []
    return p


@pytest.fixture(autouse=True)
def _clean_bg():
    bg._jobs.clear()
    bg._event_loop = None
    bg._finish_event = None
    yield
    bg._jobs.clear()
    bg._event_loop = None
    bg._finish_event = None


async def test_empty_buffer_resumes_on_bg_finish():
    bg.register_event_loop(asyncio.get_running_loop())
    p = _make_prompt(buffer_text="")
    # симулируем завершённую фоновую задачу
    job = bg._Job(id="bg-1", command="echo x", status="done", output="ok")
    bg._jobs["bg-1"] = job

    async def fire():
        await asyncio.sleep(0.05)
        bg.get_finish_event().set()

    asyncio.ensure_future(fire())
    result = await asyncio.wait_for(p._read_with_bg_resume(None), timeout=3)
    assert result is _BG_RESUME
    # app.exit вызван чтобы снять prompt
    assert p._session.app.exited_with == ""


async def test_typing_user_not_interrupted():
    bg.register_event_loop(asyncio.get_running_loop())
    p = _make_prompt(buffer_text="набираю команду")  # буфер НЕ пуст
    job = bg._Job(id="bg-1", command="echo x", status="done", output="ok")
    bg._jobs["bg-1"] = job

    async def fire_then_submit():
        await asyncio.sleep(0.05)
        bg.get_finish_event().set()       # bg завершилась — но юзер печатает
        await asyncio.sleep(0.1)
        p._session.resolve("итоговый ввод")  # юзер дожал Enter

    asyncio.ensure_future(fire_then_submit())
    result = await asyncio.wait_for(p._read_with_bg_resume(None), timeout=3)
    # ожидание НЕ прервано bg-резюмом — вернулся реальный ввод
    assert result == "итоговый ввод"
    assert p._session.app.exited_with is None


async def test_real_input_takes_priority():
    bg.register_event_loop(asyncio.get_running_loop())
    p = _make_prompt(buffer_text="")

    async def submit():
        await asyncio.sleep(0.05)
        p._session.resolve("привет")

    asyncio.ensure_future(submit())
    result = await asyncio.wait_for(p._read_with_bg_resume(None), timeout=3)
    assert result == "привет"


async def test_no_bridge_plain_wait():
    # Event не привязан (get_finish_event() is None) → обычное ожидание ввода.
    assert bg.get_finish_event() is None
    p = _make_prompt(buffer_text="")

    async def submit():
        await asyncio.sleep(0.05)
        p._session.resolve("ввод")

    asyncio.ensure_future(submit())
    result = await asyncio.wait_for(p._read_with_bg_resume(None), timeout=3)
    assert result == "ввод"


async def test_spurious_signal_no_pending_keeps_waiting():
    # Event взвёлся, но недоставленных задач нет → не прерываем, ждём ввод.
    bg.register_event_loop(asyncio.get_running_loop())
    p = _make_prompt(buffer_text="")

    async def fire_then_submit():
        await asyncio.sleep(0.05)
        bg.get_finish_event().set()       # ложный сигнал (нет pending)
        await asyncio.sleep(0.1)
        p._session.resolve("ввод после ложного сигнала")

    asyncio.ensure_future(fire_then_submit())
    result = await asyncio.wait_for(p._read_with_bg_resume(None), timeout=3)
    assert result == "ввод после ложного сигнала"
