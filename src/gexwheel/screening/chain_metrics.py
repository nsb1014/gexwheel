"""Shared option-chain metric helpers.

Single implementation used by both screening.filters (daily gate) and
screening.primary (periodic screen) so the two can never disagree.
"""
from __future__ import annotations

from datetime import date

from ..models import OptionQuote

_MIN_DTE = 7
_N_STRIKES = 3


def eligible_expiries(quotes: list[OptionQuote], asof: date,
                      min_dte: int = _MIN_DTE) -> list[date]:
    """Expiries with DTE > min_dte, sorted nearest-first."""
    return sorted(
        {q.expiry for q in quotes if (q.expiry - asof).days > min_dte},
        key=lambda e: (e - asof).days,
    )


def near_oi_sum(quotes: list[OptionQuote], spot: float, asof: date,
                n_strikes: int = _N_STRIKES) -> int:
    """Sum OI (calls+puts) over the `n_strikes` strikes nearest spot on the
    nearest eligible expiry. 0 if no eligible expiry."""
    exps = eligible_expiries(quotes, asof)
    if not exps:
        return 0
    near = [q for q in quotes if q.expiry == exps[0]]
    strikes_sorted = sorted({q.strike for q in near}, key=lambda s: abs(s - spot))
    top = set(strikes_sorted[:n_strikes])
    return sum(q.open_interest for q in near if q.strike in top)


def atm_call_spread(quotes: list[OptionQuote], spot: float, asof: date
                    ) -> tuple[float | None, str]:
    """Return (spread_pct, status). status is 'no_quote' when there is no usable
    ATM call (none on the nearest eligible expiry, or bid==ask==0), else 'ok'."""
    exps = eligible_expiries(quotes, asof)
    if not exps:
        return None, "no_quote"
    near_calls = [q for q in quotes if q.expiry == exps[0] and q.kind == "C"]
    if not near_calls:
        return None, "no_quote"
    atm = min(near_calls, key=lambda q: abs(q.strike - spot))
    if atm.bid == 0 and atm.ask == 0:
        return None, "no_quote"
    return atm.spread_pct, "ok"
