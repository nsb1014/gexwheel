"""Mention-velocity math. FULLY IMPLEMENTED.

velocity = mentions_today / avg(mentions over trailing window, EXCLUDING today)

Guards (from the spec discussed in chat):
  * baseline_floor: trailing avg must be >= floor, else never triggers
    (kills the 2 -> 8 mentions divide-by-small noise)
  * min_history_days: need enough history to trust the baseline
  * max_daily_mentions: baselines above this are permanent meme residents
    (GME-tier) - velocity is meaningless there, never trigger
"""
from __future__ import annotations

from ..models import VelocityResult


def mention_velocity(symbol: str, today: int, history: list[int], *,
                     trigger: float = 3.0, baseline_floor: int = 10,
                     min_history_days: int = 5,
                     max_daily_mentions: int = 1000) -> VelocityResult:
    """`history` = prior daily mention counts, most recent first, today EXCLUDED."""
    if len(history) < min_history_days:
        return VelocityResult(symbol, today, 0.0, 0.0, False)

    baseline = sum(history) / len(history)
    ratio = (today / baseline) if baseline > 0 else 0.0
    triggered = (
        baseline >= baseline_floor
        and baseline <= max_daily_mentions
        and ratio >= trigger
    )
    return VelocityResult(symbol, today, baseline, ratio, triggered)
