"""YFinance chain adapter behavior."""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

from gexwheel.data.chains import YFinanceChains


def test_spot_fallback_uses_one_day_history_period():
    class FakeHistory:
        empty = False

        def __getitem__(self, key):
            assert key == "Close"
            return SimpleNamespace(iloc=[12.34])

    class FakeTicker:
        fast_info = {}

        def __init__(self):
            self.periods = []

        def history(self, period):
            self.periods.append(period)
            return FakeHistory()

    ticker = FakeTicker()

    assert YFinanceChains()._get_spot(ticker, "TEST") == 12.34
    assert ticker.periods == ["1d"]


def test_fetch_logs_yfinance_open_interest_timing_at_debug(caplog, monkeypatch):
    class FakeTicker:
        fast_info = {"last_price": 20.0}
        options = []

    monkeypatch.setattr(YFinanceChains, "_get_ticker", lambda self, symbol: FakeTicker())
    with caplog.at_level(logging.DEBUG, logger="gexwheel.data.chains"):
        YFinanceChains(request_sleep_s=0).fetch("TEST", date(2026, 6, 10), max_dte=60)

    assert "yfinance OI updates once daily" in caplog.text


def test_fetch_retries_one_day_history_spot_fallback(monkeypatch):
    class FakeHistory:
        empty = False

        def __getitem__(self, key):
            return SimpleNamespace(iloc=[12.34])

    class FakeTicker:
        fast_info = {}

        def __init__(self):
            self.periods = []

        def history(self, period):
            self.periods.append(period)
            if len(self.periods) == 1:
                raise RuntimeError("transient history failure")
            return FakeHistory()

        @property
        def options(self):
            return []

    ticker = FakeTicker()
    monkeypatch.setattr("gexwheel.data.chains.yf.Ticker", lambda symbol: ticker)
    monkeypatch.setattr("gexwheel.data.chains.time.sleep", lambda seconds: None)

    spot, quotes = YFinanceChains(request_sleep_s=0, request_retries=2).fetch(
        "TEST", date(2026, 6, 10), max_dte=60
    )

    assert spot == 12.34
    assert quotes == []
    assert ticker.periods == ["1d", "1d"]
