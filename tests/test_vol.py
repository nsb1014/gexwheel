"""Volatility analytics known-answer tests."""
from __future__ import annotations

from gexwheel.analytics.vol import iv_rank, realized_vol


def test_realized_vol_of_constant_series_is_zero():
    assert realized_vol([10.0] * 21) == 0.0


def test_iv_rank_of_new_max_is_approximately_100():
    history = [0.20 + i * 0.01 for i in range(20)]

    assert iv_rank(0.50, history) == 100.0
