"""tools/background.py — фоновые задачи и мост поток→asyncio для авто-резюма."""

import asyncio
import os

import pytest

from tools import background as bg


@pytest.fixture(autouse=True)
def _clean_jobs():
    # Изолируем глобальный реестр задач между тестами.
    bg._jobs.clear()
    bg._counter = 0
    bg._event_loop = None
    bg._finish_event = None
    yield
    bg._jobs.clear()
    bg._event_loop = None
    bg._finish_event = None


def _env():
    return os.environ.copy()


# ---------------- мост / Event ----------------
async def test_register_creates_event():
    bg.register_event_loop(asyncio.get_running_loop())
    ev = bg.get_finish_event()
    assert isinstance(ev, asyncio.Event)
    assert not ev.is_set()


async def test_finish_event_fires_on_completion():
    bg.register_event_loop(asyncio.get_running_loop())
    ev = bg.get_finish_event()
    bg.start_background("echo hi", ".", _env())
    await asyncio.wait_for(ev.wait(), timeout=5)
    assert ev.is_set()


async def test_clear_finish_event_resets():
    bg.register_event_loop(asyncio.get_running_loop())
    ev = bg.get_finish_event()
    bg.start_background("true", ".", _env())
    await asyncio.wait_for(ev.wait(), timeout=5)
    bg.clear_finish_event()
    assert not ev.is_set()


async def test_signal_safe_without_loop():
    # Нет привязанного loop — _signal_finish не должен падать.
    assert bg.get_finish_event() is None
    bg._signal_finish()  # не бросает


# ---------------- has_pending_finished ----------------
async def test_has_pending_finished_lifecycle():
    bg.register_event_loop(asyncio.get_running_loop())
    ev = bg.get_finish_event()
    assert bg.has_pending_finished() is False
    bg.start_background("echo x", ".", _env())
    await asyncio.wait_for(ev.wait(), timeout=5)
    assert bg.has_pending_finished() is True
    # после drain — недоставленных нет
    bg.drain_finished_results()
    assert bg.has_pending_finished() is False


async def test_running_job_not_pending():
    bg.register_event_loop(asyncio.get_running_loop())
    bg.start_background("sleep 5", ".", _env())
    # сразу после старта задача running → не pending
    assert bg.has_pending_finished() is False


# ---------------- drain (существующее поведение) ----------------
async def test_drain_returns_result_once():
    bg.register_event_loop(asyncio.get_running_loop())
    ev = bg.get_finish_event()
    bg.start_background("echo hello", ".", _env())
    await asyncio.wait_for(ev.wait(), timeout=5)
    first = bg.drain_finished_results()
    assert len(first) == 1
    assert first[0].status == "ok"
    assert "hello" in first[0].output
    # повторный drain пуст (delivered=True)
    assert bg.drain_finished_results() == []


async def test_drain_marks_error_status():
    bg.register_event_loop(asyncio.get_running_loop())
    ev = bg.get_finish_event()
    bg.start_background("exit 3", ".", _env())
    await asyncio.wait_for(ev.wait(), timeout=5)
    res = bg.drain_finished_results()
    assert len(res) == 1
    assert res[0].status == "error"
    assert res[0].exit_code == 3


async def test_start_returns_job_id():
    bg.register_event_loop(asyncio.get_running_loop())
    jid = bg.start_background("true", ".", _env())
    assert jid.startswith("bg-")
