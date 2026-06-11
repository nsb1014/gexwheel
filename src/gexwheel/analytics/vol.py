"""Volatility stats: ATM IV, IV rank, realized vol, VRP.

TODO(sonnet): implement all three functions. Specs:

realized_vol(closes, window=20) -> float
  * closes: list[float], oldest first, daily closes (>= window+1 values required;
    raise ValueError otherwise).
  * log returns r_i = ln(c_i / c_{i-1}) over the last `window` returns.
  * stdev (population, ddof=0 is fine) * sqrt(252), returned as decimal (0.55 = 55%).

atm_iv(quotes, spot, asof, target_dte=30) -> float | None
  * quotes: list[OptionQuote] for ONE symbol, mixed expiries/strikes.
  * pick the expiry with DTE closest to target_dte (must be > 7 DTE);
    within it, take the call and put whose strikes are nearest to spot;
    return the average of their IVs (or whichever exists). None if no usable data.

iv_rank(current_iv, iv_history) -> float | None
  * iv_history: list[float] of trailing daily atm_iv values (up to 252).
  * percentile of current_iv within history * 100, i.e.
    100 * (count(h < current_iv) / len(history)).
  * Require >= 20 history points, else return None (job stores None; the
    Stage-2 IV-rank check treats None as FAIL until enough history accrues -
    expected during the first weeks of operation. Document this in alerts).

vrp = atm_iv - realized_vol, computed by the caller (jobs/morning.py).
"""
from __future__ import annotations

import math
from datetime import date

from ..models import OptionQuote


def realized_vol(closes: list[float], window: int = 20) -> float:
    """Annualized realized vol from the last `window` log returns."""
    if len(closes) < window + 1:
        raise ValueError(f"need >= {window + 1} closes, got {len(closes)}")
    tail = closes[-(window + 1):]
    log_rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    mean = sum(log_rets) / len(log_rets)
    variance = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)   # ddof=0
    return math.sqrt(variance) * math.sqrt(252)


def atm_iv(quotes: list[OptionQuote], spot: float, asof: date, target_dte: int = 30) -> float | None:
    """Return average IV of the call+put nearest spot on the expiry closest to target_dte (>7 DTE)."""
    expiries = sorted({q.expiry for q in quotes
                       if (q.expiry - asof).days > 7
                       and (q.expiry - asof).days <= 365},
                      key=lambda e: abs((e - asof).days - target_dte))
    for exp in expiries:
        chain = [q for q in quotes if q.expiry == exp and q.iv > 0]
        calls = sorted([q for q in chain if q.kind == "C"], key=lambda q: abs(q.strike - spot))
        puts  = sorted([q for q in chain if q.kind == "P"], key=lambda q: abs(q.strike - spot))
        ivs = []
        if calls:
            ivs.append(calls[0].iv)
        if puts:
            ivs.append(puts[0].iv)
        if ivs:
            return sum(ivs) / len(ivs)
    return None


def iv_rank(current_iv: float, iv_history: list[float]) -> float | None:
    """IV percentile rank 0-100 vs trailing history. None if history < 20 points."""
    if len(iv_history) < 20:
        return None
    below = sum(1 for h in iv_history if h < current_iv)
    return 100.0 * below / len(iv_history)
