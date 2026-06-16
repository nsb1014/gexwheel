"""Shared chain-metric helpers used by both filters and the primary screen."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel.models import OptionQuote
from gexwheel.screening.chain_metrics import (
    atm_call_spread,
    eligible_expiries,
    near_oi_sum,
)

ASOF = date(2026, 6, 10)
EXP = ASOF + timedelta(days=30)
SOON = ASOF + timedelta(days=3)  # <= 7 DTE -> ineligible


def _q(strike, kind, oi, bid=0.95, ask=1.00, exp=EXP):
    return OptionQuote("T", strike, exp, kind, oi, 0.5, bid, ask)


def test_eligible_expiries_excludes_le_7_dte():
    quotes = [_q(100, "C", 10, exp=SOON), _q(100, "C", 10, exp=EXP)]
    assert eligible_expiries(quotes, ASOF) == [EXP]


def test_near_oi_sum_uses_three_nearest_strikes_nearest_expiry():
    quotes = [
        _q(90, "C", 100), _q(95, "C", 100), _q(100, "C", 100),
        _q(105, "C", 100), _q(200, "C", 9999),  # far strike excluded
        _q(100, "P", 100), _q(95, "P", 100), _q(90, "P", 100),
    ]
    # Nearest 3 strikes to spot=100: {100, 95, 105}; put at 90 excluded
    assert near_oi_sum(quotes, 100.0, ASOF) == 500  # 3 calls + 2 puts at those strikes


def test_atm_call_spread_no_quote_when_bid_ask_zero():
    quotes = [_q(100, "C", 100, bid=0.0, ask=0.0)]
    sp, status = atm_call_spread(quotes, 100.0, ASOF)
    assert status == "no_quote" and sp is None


def test_atm_call_spread_ok():
    quotes = [_q(100, "C", 100, bid=0.90, ask=1.10)]
    sp, status = atm_call_spread(quotes, 100.0, ASOF)
    assert status == "ok"
    assert abs(sp - 0.2) < 1e-9  # (1.10-0.90)/1.0
