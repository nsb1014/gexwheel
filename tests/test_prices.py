"""Price-history retry behavior and earnings-date selection (no live network)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from gexwheel.data.prices import daily_closes, next_earnings


def test_daily_closes_retries_transient_history_failure(monkeypatch):
    calls = {"n": 0}

    class FakeTicker:
        def history(self, period):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("throttled")
            return pd.DataFrame({"Close": [10.0] * 70})

    monkeypatch.setattr("gexwheel.data.prices.yf.Ticker", lambda symbol: FakeTicker())
    # patch the stdlib module, not gexwheel.data.prices.time: before the fix
    # the module doesn't import time, and the test must fail on behavior
    # (no retry), not on a monkeypatch AttributeError
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    closes = daily_closes("TEST")

    assert len(closes) == 70
    assert calls["n"] == 2


def test_next_earnings_picks_first_future_date(monkeypatch):
    class FakeTicker:
        calendar = {"Earnings Date": [date(2020, 1, 1), date(2099, 1, 2), date(2099, 1, 1)]}

    monkeypatch.setattr("gexwheel.data.prices.yf.Ticker", lambda symbol: FakeTicker())

    assert next_earnings("TEST") == date(2099, 1, 1)
