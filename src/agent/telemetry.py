"""Turn and round lifecycle tracking for structured logging.

Provides helpers to manage correlation context across agent turns and rounds,
and to emit turn-level summaries with timing breakdowns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from logger import bind, info, unbind


@dataclass
class TurnStats:
    """Accumulates metrics for a single user turn (one user message → final response)."""

    turn_id: int
    start_time: float = field(default_factory=time.monotonic)

    # Round tracking
    rounds: int = 0

    # API metrics
    api_calls: int = 0
    api_time: float = 0.0
    api_ttfb_total: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    # Tool metrics
    tool_calls: int = 0
    tool_time: float = 0.0

    # Context metrics
    context_time: float = 0.0

    # Round gap tracking
    _last_round_end: float = 0.0
    _round_gap_total: float = 0.0

    def end_round(self, round_time: float) -> None:
        """Record end of a round and track gap from previous round."""
        now = time.monotonic()
        if self._last_round_end > 0:
            gap = now - self._last_round_end
            if gap > 2.0:  # SLOW_ROUND_GAP_THRESHOLD
                from logger import warning

                warning("round.gap.slow", gap=gap, round=self.rounds)
            self._round_gap_total += gap
        self._last_round_end = now

    def add_api_call(
        self,
        duration: float,
        ttfb: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record an API request."""
        self.api_calls += 1
        self.api_time += duration
        self.api_ttfb_total += ttfb
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def add_tool_call(self, duration: float) -> None:
        """Record a tool execution."""
        self.tool_calls += 1
        self.tool_time += duration

    def add_context_time(self, duration: float) -> None:
        """Record context building/pruning time."""
        self.context_time += duration

    def summary(self) -> dict[str, Any]:
        """Generate turn summary dict."""
        total_duration = time.monotonic() - self.start_time
        local_overhead = (
            total_duration - self.api_time - self.tool_time - self.context_time
        )

        return {
            "duration": total_duration,
            "rounds": self.rounds,
            "api_calls": self.api_calls,
            "api_time": self.api_time,
            "tool_calls": self.tool_calls,
            "tool_time": self.tool_time,
            "context_time": self.context_time,
            "local_overhead": max(0.0, local_overhead),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


# Global turn counter
_turn_counter = 0


def start_turn(session_id: str = "") -> TurnStats:
    """Start a new turn (one user message). Binds correlation context."""
    global _turn_counter
    _turn_counter += 1

    bind(session=session_id, turn=_turn_counter, round=0)
    stats = TurnStats(turn_id=_turn_counter)

    info("agent.turn.start", session=session_id or "<none>")
    return stats


def start_round(stats: TurnStats) -> None:
    """Start a new round within the current turn."""
    stats.rounds += 1
    bind(round=stats.rounds)
    info("agent.round.start", round=stats.rounds)


def end_round(stats: TurnStats, round_time: float) -> None:
    """End the current round."""
    stats.end_round(round_time)
    info("agent.round.end", round=stats.rounds, duration=round_time)


def end_turn(stats: TurnStats) -> None:
    """End the turn and emit summary."""
    summary = stats.summary()
    info("agent.turn.end", **summary)

    # Clear round from context (turn stays for any post-turn logging)
    unbind("round")


__all__ = [
    "TurnStats",
    "end_round",
    "end_turn",
    "start_round",
    "start_turn",
]
