"""YFinance chain adapter behavior."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd

from gexwheel.data.chains import YFinanceChains

ASOF = date(2026, 6, 10)
EXP_STR = (ASOF + timedelta(days=30)).isoformat()

_COLS = ["strike", "openInterest", "impliedVolatility", "bid", "ask"]


def _ticker_with_chain(calls: pd.DataFrame, puts: pd.DataFrame):
    class FakeTicker:
        fast_info = {"last_price": 100.0}
        options = [EXP_STR]

        def option_chain(self, exp):
            return SimpleNamespace(calls=calls, puts=puts)

    return FakeTicker()


def _fetch(monkeypatch, calls: pd.DataFrame, puts: pd.DataFrame):
    ticker = _ticker_with_chain(calls, puts)
    monkeypatch.setattr(YFinanceChains, "_get_ticker", lambda self, symbol: ticker)
    return YFinanceChains(request_sleep_s=0).fetch("TEST", ASOF, max_dte=60)


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


def test_nan_open_interest_coerced_to_zero_not_dropped(monkeypatch):
    calls = pd.DataFrame([{"strike": 100.0, "openInterest": float("nan"),
                           "impliedVolatility": 0.5, "bid": 1.0, "ask": 1.1}])
    puts = pd.DataFrame([], columns=_COLS)

    _, quotes = _fetch(monkeypatch, calls, puts)

    assert len(quotes) == 1
    assert quotes[0].open_interest == 0
    assert quotes[0].iv == 0.5


def test_nan_bid_ask_coerced_to_zero(monkeypatch):
    calls = pd.DataFrame([{"strike": 100.0, "openInterest": 250,
                           "impliedVolatility": 0.4,
                           "bid": float("nan"), "ask": float("nan")}])
    puts = pd.DataFrame([], columns=_COLS)

    _, quotes = _fetch(monkeypatch, calls, puts)

    assert len(quotes) == 1
    assert quotes[0].bid == 0.0
    assert quotes[0].ask == 0.0


def test_row_with_nan_oi_and_nan_iv_is_skipped_as_dead_strike(monkeypatch):
    calls = pd.DataFrame([{"strike": 100.0, "openInterest": float("nan"),
                           "impliedVolatility": float("nan"),
                           "bid": 1.0, "ask": 1.1}])
    puts = pd.DataFrame([], columns=_COLS)

    _, quotes = _fetch(monkeypatch, calls, puts)

    assert quotes == []
