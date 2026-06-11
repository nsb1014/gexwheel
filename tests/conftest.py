"""Shared fixtures: a synthetic option chain with engineered walls."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from gexwheel.models import OptionQuote

ASOF = date(2026, 6, 10)
SPOT = 100.0
EXP = ASOF + timedelta(days=30)


@pytest.fixture
def synthetic_chain() -> list[OptionQuote]:
    """Heavy call OI at 110 (call wall), heavy put OI at 90 (put wall),
    light noise elsewhere. IVs flat at 50%."""
    q = []
    for strike, c_oi, p_oi in [
        (80, 50, 300), (90, 100, 5000), (95, 200, 800),
        (100, 600, 600), (105, 900, 200), (110, 6000, 100), (120, 400, 50),
    ]:
        q.append(OptionQuote("TEST", strike, EXP, "C", c_oi, 0.50, 1.0, 1.1))
        q.append(OptionQuote("TEST", strike, EXP, "P", p_oi, 0.50, 1.0, 1.1))
    return q
