"""Price history + simple derived stats via yfinance.

daily_closes(symbol, lookback_days=120) -> list[float]
  * yfinance.Ticker(symbol).history(period=f"{lookback_days}d")["Close"]
  * oldest first, drop NaN. Raise PriceFetchError if < 60 rows.

sma(closes, window) -> float
  * plain arithmetic mean of the last `window` closes (ValueError if short).

next_earnings(symbol) -> date | None
  * yfinance Ticker.calendar / get_earnings_dates(limit=4): return the next
    FUTURE earnings date or None. yfinance is flaky here - wrap everything
    in try/except and return None on any failure (filters treat None as
    'unknown', which PASSES the blackout check but sets values['earnings']
    = 'unknown' so it shows in the Discord card).

sector(symbol) -> str | None
  * Ticker.info.get('sector') / .get('industry'). Cache in tickers table
    via db.upsert_ticker - .info is a slow scrape, call at most once per
    symbol per week. Return industry string when sector is missing (the
    biotech exclusion matches against BOTH, case-insensitive substring).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import yfinance as yf

log = logging.getLogger(__name__)


class PriceFetchError(RuntimeError):
    pass


def daily_closes(symbol: str, lookback_days: int = 120) -> list[float]:
    """Return daily closes oldest-first, raises PriceFetchError if < 60 rows."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=f"{lookback_days}d")
    except Exception as exc:
        raise PriceFetchError(f"price history failed for {symbol}: {exc}") from exc

    if hist.empty:
        raise PriceFetchError(f"no price history for {symbol}")

    closes = [float(c) for c in hist["Close"].dropna()]
    if len(closes) < 60:
        raise PriceFetchError(f"{symbol}: only {len(closes)} closes, need >= 60")

    return closes


def sma(closes: list[float], window: int) -> float:
    """Simple moving average of the last `window` closes."""
    if len(closes) < window:
        raise ValueError(f"need >= {window} closes for SMA, got {len(closes)}")
    tail = closes[-window:]
    return sum(tail) / len(tail)


def next_earnings(symbol: str) -> date | None:
    """Return the next future earnings date or None on any failure / no data."""
    try:
        ticker = yf.Ticker(symbol)
        # try calendar first (most reliable)
        cal = ticker.calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                # may be a list or single value
                if not isinstance(ed, (list, tuple)):
                    ed = [ed]
                today = datetime.utcnow().date()
                future = sorted(
                    [d.date() if hasattr(d, "date") else d for d in ed
                     if (d.date() if hasattr(d, "date") else d) > today]
                )
                if future:
                    return future[0]

        # fallback: get_earnings_dates
        eds = ticker.get_earnings_dates(limit=8)
        if eds is not None and not eds.empty:
            today = datetime.utcnow().date()
            future_dates = sorted([
                idx.date() if hasattr(idx, "date") else idx
                for idx in eds.index
                if (idx.date() if hasattr(idx, "date") else idx) > today
            ])
            if future_dates:
                return future_dates[0]
    except Exception as exc:
        log.debug("next_earnings(%s) failed: %s", symbol, exc)
    return None


def sector(symbol: str) -> str | None:
    """Return sector string from yfinance info (slow scrape, cache in DB)."""
    try:
        info = yf.Ticker(symbol).info
        return info.get("sector") or info.get("industry") or None
    except Exception as exc:
        log.debug("sector(%s) failed: %s", symbol, exc)
        return None
