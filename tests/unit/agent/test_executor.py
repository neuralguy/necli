"""agent/executor.py — вспомогательная логика рендера/тайминга."""

import asyncio
import contextvars

from agent.executor import _extract_write_time


class TestExecutorPreservesContextVars:
    """Регрессия: tool-инструменты исполняются через loop.run_in_executor (поток
    пула). ContextVars (рабочая директория necli_working_dir) НЕ переносятся в
    поток автоматически — без copy_context относительные пути резолвились от cwd
    процесса, а не от --workdir. Главный агент видел File not found на первом read."""

    def test_executor_carries_contextvar_into_thread(self):
        # Воспроизводим механику: значение ContextVar, установленное в корутине,
        # должно быть видно функции, исполняемой в run_in_executor, ТОЛЬКО при
        # копировании контекста (как теперь делает _execute_single-обёртка).
        var = contextvars.ContextVar("test_wd", default="DEFAULT")

        async def main():
            var.set("SET_IN_COROUTINE")
            loop = asyncio.get_running_loop()
            # без копирования — поток видит дефолт
            no_copy = await loop.run_in_executor(None, var.get)
            # с копированием — поток видит установленное значение
            ctx = contextvars.copy_context()
            copied = await loop.run_in_executor(None, lambda: ctx.run(var.get))
            return no_copy, copied

        no_copy, copied = asyncio.run(main())
        assert no_copy == "DEFAULT"
        assert copied == "SET_IN_COROUTINE"

    def test_executor_uses_copy_context(self):
        # Сам executor должен оборачивать вызов в copy_context().run.
        import inspect

        import agent.executor as ex

        src = inspect.getsource(ex)
        assert "copy_context()" in src
        assert "ctx.run(" in src


class TestExtractWriteTime:
    """Регрессия: финальный статичный вывод write показывал 0.0s, т.к. брал
    мгновенное время исполнения вместо streaming-времени (@@WRITE_TIME=N@@)."""

    def test_extracts_marker(self):
        sub = "[dim]~12 tk · $0.0010 · @@WRITE_TIME=2.34@@[/dim]"
        assert _extract_write_time(sub) == 2.34

    def test_zero(self):
        assert _extract_write_time("@@WRITE_TIME=0.00@@") == 0.0

    def test_no_marker(self):
        assert _extract_write_time("[dim]~12 tk · $0.0010[/dim]") is None

    def test_empty(self):
        assert _extract_write_time("") is None

    def test_malformed(self):
        assert _extract_write_time("@@WRITE_TIME=abc@@") is None
