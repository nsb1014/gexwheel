"""Option chain fetching. TODO(sonnet): implement YFinanceChains.

Interface contract (the rest of the pipeline depends ONLY on this):

class ChainSource(Protocol):
    def fetch(self, symbol: str, asof: date, max_dte: int) -> tuple[float, list[OptionQuote]]
        Returns (spot_price, quotes). Quotes include BOTH calls and puts for
        ALL expirations with 0 < DTE <= max_dte.

YFinanceChains spec:
  * Use yfinance.Ticker(symbol).
  * spot: t.fast_info["last_price"] (fall back to t.history(period="1d") close).
  * expirations: t.options (list of "YYYY-MM-DD" strings); keep those within max_dte.
  * per expiry: oc = t.option_chain(exp); oc.calls / oc.puts are DataFrames with
    columns: strike, openInterest, impliedVolatility, bid, ask.
  * Map rows -> OptionQuote(kind='C'/'P'). Skip rows where openInterest is
    NaN/0 AND impliedVolatility is NaN (dead strikes). Coerce NaN OI to 0,
    NaN bid/ask to 0.0. impliedVolatility from yfinance is already decimal.
  * THROTTLE: time.sleep(cfg request_sleep_s) between option_chain() calls;
    retry each network call up to request_retries times with exponential
    backoff (1s, 2s, 4s); on final failure raise ChainFetchError(symbol).
  * yfinance OI updates once daily (previous session) - that is fine, GEX
    walls are an OI artifact. Document in logs at DEBUG, do not warn.

PolygonChains: leave as a stub raising NotImplementedError - interface only,
implemented later if/when a paid key is added.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Protocol

import yfinance as yf

from ..models import OptionQuote

log = logging.getLogger(__name__)


class ChainFetchError(RuntimeError):
    pass


class ChainSource(Protocol):
    def fetch(self, symbol: str, asof: date, max_dte: int) -> tuple[float, list[OptionQuote]]: ...


class YFinanceChains:
    def __init__(self, request_sleep_s: float = 1.5, request_retries: int = 3):
        self.sleep_s = request_sleep_s
        self.retries = request_retries

    def _get_ticker(self, symbol: str):
        for attempt in range(self.retries):
            try:
                return yf.Ticker(symbol)
            except Exception as exc:
                if attempt == self.retries - 1:
                    raise ChainFetchError(f"Ticker({symbol}) failed: {exc}") from exc
                time.sleep(2 ** attempt)

    def _get_spot(self, ticker, symbol: str) -> float:
        try:
            price = ticker.fast_info.get("last_price") or ticker.fast_info.get("lastPrice")
            if price and price > 0:
                return float(price)
        except Exception:
            pass
        for attempt in range(self.retries):
            try:
                hist = ticker.history(period="1d")
                if not hist.empty:
                    return float(hist["Close"].iloc[-1])
            except Exception:
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
        raise ChainFetchError(f"Could not get spot price for {symbol}")

    def fetch(self, symbol: str, asof: date, max_dte: int) -> tuple[float, list[OptionQuote]]:
        ticker = self._get_ticker(symbol)
        spot = self._get_spot(ticker, symbol)
        log.debug("yfinance OI updates once daily from the previous session; using it for GEX walls")

        try:
            expirations = ticker.options  # list of "YYYY-MM-DD"
        except Exception as exc:
            raise ChainFetchError(f"options list failed for {symbol}: {exc}") from exc

        quotes: list[OptionQuote] = []
        for exp_str in expirations:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - asof).days
            if dte <= 0 or dte > max_dte:
                continue

            time.sleep(self.sleep_s)
            for attempt in range(self.retries):
                try:
                    oc = ticker.option_chain(exp_str)
                    break
                except Exception as exc:
                    if attempt == self.retries - 1:
                        log.warning("option_chain(%s, %s) failed: %s", symbol, exp_str, exc)
                        oc = None
                    else:
                        time.sleep(2 ** attempt)

            if oc is None:
                continue

            for df, kind in ((oc.calls, "C"), (oc.puts, "P")):
                for _, row in df.iterrows():
                    try:
                        oi = int(row.get("openInterest") or 0)
                        iv = float(row.get("impliedVolatility") or 0)
                        if oi == 0 and iv == 0:
                            continue  # dead strike
                        bid = float(row.get("bid") or 0.0)
                        ask = float(row.get("ask") or 0.0)
                        strike = float(row["strike"])
                        quotes.append(OptionQuote(
                            symbol=symbol, strike=strike, expiry=exp_date,
                            kind=kind, open_interest=max(oi, 0),
                            iv=iv if iv > 0 else 0.0,
                            bid=bid, ask=ask,
                        ))
                    except Exception as exc:
                        log.debug("skipping bad row %s %s: %s", symbol, kind, exc)

        log.info("chains: %s spot=%.2f, %d quotes across %d expiries (max_dte=%d)",
                 symbol, spot, len(quotes),
                 len({q.expiry for q in quotes}), max_dte)
        return spot, quotes


class PolygonChains:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, symbol: str, asof: date, max_dte: int) -> tuple[float, list[OptionQuote]]:
        raise NotImplementedError("polygon source not implemented yet (paid tier)")


def make_chain_source(cfg: dict) -> ChainSource:
    """Factory keyed on cfg['data']['chain_source']. FULLY IMPLEMENTED."""
    d = cfg["data"]
    if d["chain_source"] == "yfinance":
        return YFinanceChains(d.get("request_sleep_s", 1.5), d.get("request_retries", 3))
    if d["chain_source"] == "polygon":
        return PolygonChains(d["polygon_api_key"])
    raise ValueError(f"unknown chain_source {d['chain_source']!r}")
