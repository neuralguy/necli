"""workflows/runner.py — ctx.pipeline пропускает items через стадии независимо."""

import asyncio

from workflows.runner import WorkflowContext


def _ctx():
    # pipeline не трогает runner, когда стадии — обычные async-функции (не agent-calls)
    return WorkflowContext(runner=None)


class TestPipeline:
    def test_each_item_through_all_stages(self):
        ctx = _ctx()

        async def main():
            return await ctx.pipeline(
                [1, 2, 3],
                lambda v, i, s: v + 10,          # стадия 1
                lambda v, i, s: v * 2,           # стадия 2
            )

        out = asyncio.run(main())
        # (1+10)*2=22, (2+10)*2=24, (3+10)*2=26
        assert out == [22, 24, 26]

    def test_stages_flow_independently_not_barrier(self):
        # Доказываем отсутствие барьера: item с быстрой стадией 1 входит в стадию 2
        # ДО того, как медленный item закончит стадию 1. Если бы был барьер, все
        # стадии-1 завершились бы раньше любой стадии-2.
        ctx = _ctx()
        events = []

        def stage1(v, i, s):
            async def run():
                # item 0 медленный в стадии 1, item 1 быстрый
                await asyncio.sleep(0.05 if v == "slow" else 0.0)
                events.append(f"s1:{v}")
                return v
            return run()

        def stage2(v, i, s):
            async def run():
                events.append(f"s2:{v}")
                return v
            return run()

        async def main():
            return await ctx.pipeline(["slow", "fast"], stage1, stage2)

        asyncio.run(main())
        # fast проходит s1 и s2 ПОКА slow ещё в s1 → s2:fast раньше s1:slow.
        assert events.index("s2:fast") < events.index("s1:slow"), (
            f"pipeline повёл себя как барьер: {events}"
        )

    def test_empty_items(self):
        ctx = _ctx()
        assert asyncio.run(ctx.pipeline([], lambda v, i, s: v)) == []


class TestParallelIsBarrier:
    def test_parallel_waits_for_all(self):
        # parallel — барьер: возвращает результаты ТОЛЬКО когда все готовы,
        # в исходном порядке (в отличие от pipeline).
        ctx = _ctx()
        done = []

        def mk(v, delay):
            async def run():
                await asyncio.sleep(delay)
                done.append(v)
                return v
            return run()

        async def main():
            # slow первым в списке, fast вторым — но parallel ждёт обоих
            return await ctx.parallel([lambda: mk("slow", 0.04), lambda: mk("fast", 0.0)])

        out = asyncio.run(main())
        # порядок результатов сохранён (slow, fast), хотя fast завершился первым
        assert out == ["slow", "fast"]
        # оба реально выполнились (барьер дождался медленного)
        assert set(done) == {"slow", "fast"}

    def test_parallel_exception_becomes_none(self):
        # Упавший элемент не валит весь parallel — становится None.
        ctx = _ctx()

        def boom():
            async def run():
                raise ValueError("x")
            return run()

        def ok():
            async def run():
                return "ok"
            return run()

        out = asyncio.run(ctx.parallel([boom, ok]))
        assert out == [None, "ok"]
