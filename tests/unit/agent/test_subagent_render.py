"""Тесты двухпанельного рендера субагентов и сбора токенов."""

from agent.subagent_render import (
    SubagentBuffer,
    SubagentTracker,
    _fmt_tokens,
)


def _buf(index, phase, label, status="tools", total=0):
    b = SubagentBuffer(
        index=index, mode="agent", prompt="port a module",
        model_label="Opus 4.8", phase=phase, label=label,
    )
    if total:
        b.on_usage({"input_tokens": total // 2, "output_tokens": total // 2, "total_tokens": total})
    b.status = status
    return b


class TestFmtTokens:
    def test_below_1k(self):
        assert _fmt_tokens(0) == "0"
        assert _fmt_tokens(940) == "940"

    def test_thousands(self):
        assert _fmt_tokens(25800) == "25.8k"
        assert _fmt_tokens(1000) == "1.0k"

    def test_large_thousands_no_decimal(self):
        assert _fmt_tokens(150000) == "150k"

    def test_millions(self):
        assert _fmt_tokens(1_200_000) == "1.2M"


class TestOnUsage:
    def test_accumulates_across_calls(self):
        b = _buf(0, "P1", "x")
        b.on_usage({"input_tokens": 100, "output_tokens": 40, "total_tokens": 140})
        b.on_usage({"input_tokens": 60, "output_tokens": 10, "total_tokens": 70})
        assert b.input_tokens == 160
        assert b.output_tokens == 50
        assert b.total_tokens == 210

    def test_total_falls_back_to_sum(self):
        b = _buf(0, "P1", "x")
        b.on_usage({"input_tokens": 100, "output_tokens": 40})
        assert b.total_tokens == 140

    def test_none_is_noop(self):
        b = _buf(0, "P1", "x")
        b.on_usage(None)
        b.on_usage({})
        assert b.total_tokens == 0


class TestActivePhaseSelection:
    def test_first_incomplete_phase_is_active(self):
        bufs = [
            _buf(0, "P1", "a", status="done"),
            _buf(1, "P1", "b", status="done"),
            _buf(2, "P2", "c", status="tools"),
            _buf(3, "P3", "d", status="starting"),
        ]
        tr = SubagentTracker(bufs)
        # P1 завершена → активна P2 (первая незавершённая).
        g = tr._render_panels()
        assert g is not None
        assert tr._seen_phases() == ["P1", "P2", "P3"]

    def test_panels_used_without_phases(self):
        # Без фаз используется тот же framed layout с синтетической фазой.
        bufs = [_buf(0, "", "a"), _buf(1, "", "b")]
        tr = SubagentTracker(bufs)
        assert tr._seen_phases() == []
        assert tr._render() is not None
        assert tr._render_panels() is not None


class TestFmtClock:
    def test_seconds(self):
        assert SubagentTracker._fmt_clock(58) == "58s"

    def test_minutes(self):
        assert SubagentTracker._fmt_clock(66) == "1m06s"

    def test_hours(self):
        assert SubagentTracker._fmt_clock(3 * 3600 + 7 * 60 + 5) == "3h07m05s"


class TestAgentRow:
    def test_row_contains_tokens_and_tools(self):
        b = _buf(0, "P1", "port:tools/_paths", total=25800)
        b.on_tool_start("read", "x")
        b.on_tool_done(elapsed=1.0)
        row = b.render_agent_row(80)
        assert "25.8k tok" in row.plain
        assert "1 tool" in row.plain

    def test_zero_tokens_shows_zero(self):
        b = _buf(0, "P1", "x")
        row = b.render_agent_row(80)
        assert "0 tok" in row.plain

    def test_row_truncates_to_width(self):
        b = _buf(0, "P1", "port:" + "x" * 200, total=1000)
        row = b.render_agent_row(60)
        assert len(row.plain) <= 60
