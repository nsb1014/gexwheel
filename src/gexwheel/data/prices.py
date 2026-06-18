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
    = 'unknown' so it shows on the dashboard).

sector(symbol) -> str | None
  * Ticker.info.get('sector') / .get('industry'). Cache in tickers table
    via db.upsert_ticker - .info is a slow scrape, call at most once per
    symbol per week. Return industry string when sector is missing (the
    biotech exclusion matches against BOTH, case-insensitive substring).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import yfinance as yf

log = logging.getLogger(__name__)


class PriceFetchError(RuntimeError):
    pass


# NOTE: next_earnings()/daily_closes() signatures are frozen (no cfg access),
# so the market timezone is pinned here; schema.sql stores all dates in
# America/New_York trading-day terms.
_MARKET_TZ = ZoneInfo("America/New_York")


def _history_with_retry(ticker, period: str, retries: int = 3):
    """ticker.history() with exponential backoff (1s, 2s) on transient failures."""
    for attempt in range(retries):
        try:
            return ticker.history(period=period)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def daily_closes(symbol: str, lookback_days: int = 120) -> list[float]:
    """Return daily closes oldest-first, raises PriceFetchError if < 60 rows."""
    try:
        ticker = yf.Ticker(symbol)
        hist = _history_with_retry(ticker, f"{lookback_days}d")
    except Exception as exc:
        raise PriceFetchError(f"price history failed for {symbol}: {exc}") from exc

    if hist.empty:
        raise PriceFetchError(f"no price history for {symbol}")

    closes = [float(c) for c in hist["Close"].dropna()]
    if len(closes) < 60:
        raise PriceFetchError(f"{symbol}: only {len(closes)} closes, need >= 60")

    return closes


def daily_closes_and_volumes(
    symbol: str, lookback_days: int = 120
) -> tuple[list[float], list[float]]:
    """Oldest-first (closes, volumes) from one history() call.

    Rows with a NaN close are dropped (and their volume with them) so the two
    lists stay index-aligned. Raises PriceFetchError if < 60 usable closes.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = _history_with_retry(ticker, f"{lookback_days}d")
    except Exception as exc:
        raise PriceFetchError(f"price history failed for {symbol}: {exc}") from exc

    if hist is None or hist.empty:
        raise PriceFetchError(f"no price history for {symbol}")

    closes: list[float] = []
    volumes: list[float] = []
    for close, vol in zip(hist["Close"], hist["Volume"]):
        if close != close:  # NaN check without importing math
            continue
        closes.append(float(close))
        volumes.append(0.0 if (vol != vol) else float(vol))

    if len(closes) < 60:
        raise PriceFetchError(f"{symbol}: only {len(closes)} closes, need >= 60")
    return closes, volumes


def sma(closes: list[float], window: int) -> float:
    """Simple moving average of the last `window` closes."""
    if len(closes) < window:
        raise ValueError(f"need >= {window} closes for SMA, got {len(closes)}")
    tail = closes[-window:]
    return sum(tail) / len(tail)


def avg_volume(volumes: list[float], window: int) -> float:
    """Mean of the last `window` daily share volumes (ValueError if short)."""
    if len(volumes) < window:
        raise ValueError(f"need >= {window} volumes for avg_volume, got {len(volumes)}")
    tail = volumes[-window:]
    return sum(tail) / len(tail)


def current_spot(symbol: str) -> float:
    """Latest price via yfinance fast_info, falling back to the most recent close."""
    try:
        ticker = yf.Ticker(symbol)
        try:
            price = ticker.fast_info.get("last_price") or ticker.fast_info.get("lastPrice")
            if price and float(price) > 0:
                return float(price)
        except Exception:
            pass
        hist = _history_with_retry(ticker, "1d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as exc:
        raise PriceFetchError(f"spot fetch failed for {symbol}: {exc}") from exc
    raise PriceFetchError(f"no spot price for {symbol}")


def session_low(symbol: str) -> float:
    """Lowest traded price so far in the current regular session (America/New_York)."""
    try:
        ticker = yf.Ticker(symbol)
        hist = _history_with_retry(ticker, "1d")
        if hist is None or hist.empty:
            raise PriceFetchError(f"no intraday history for {symbol}")
        today = datetime.now(_MARKET_TZ).date()
        lows = []
        for ts, row in hist.iterrows():
            day = ts.date() if hasattr(ts, "date") else ts
            if day == today and row["Low"] == row["Low"]:
                lows.append(float(row["Low"]))
        if not lows:
            raise PriceFetchError(f"no session low yet for {symbol}")
        return min(lows)
    except PriceFetchError:
        raise
    except Exception as exc:
        raise PriceFetchError(f"session low failed for {symbol}: {exc}") from exc


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
                today = datetime.now(_MARKET_TZ).date()
                future = sorted(
                    [d.date() if hasattr(d, "date") else d for d in ed
                     if (d.date() if hasattr(d, "date") else d) > today]
                )
                if future:
                    return future[0]

        # fallback: get_earnings_dates
        eds = ticker.get_earnings_dates(limit=8)
        if eds is not None and not eds.empty:
            today = datetime.now(_MARKET_TZ).date()
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
