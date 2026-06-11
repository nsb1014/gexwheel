"""Stage 2: the hard gate. TODO(sonnet): implement run_filters.

run_filters(symbol, cfg, conn, *, spot, quotes, closes, gex_profile, asof,
            iv_rank_val, vrp_val) -> FilterReport

All inputs are passed in (no network calls in this module - keeps it unit
testable with synthetic data). Checks, ALL must pass; record each into
report.checks and the measured value into report.values:

  price_range     : cfg price_min <= spot <= price_max
  above_50dma     : spot > sma(closes, 50)        [skip if cfg flag false -> True]
  open_interest   : sum of OI across the 3 strikes nearest spot (calls+puts,
                    nearest expiry > 7 DTE) >= min_open_interest
  spread          : ATM call spread_pct <= max_spread_pct (use OptionQuote.spread_pct
                    on the call nearest spot, nearest expiry > 7 DTE; if bid/ask
                    are both 0 (yfinance off-hours quirk) mark value 'no_quote'
                    and FAIL - rerun during market hours will fix it)
  iv_rank         : iv_rank_val is not None and >= min_iv_rank
  vrp             : vrp_val is not None and >= min_vrp
  earnings        : next earnings (from earnings table) is None/unknown OR
                    > asof + earnings_blackout_days
  sector          : tickers.sector does not contain any excluded_sectors
                    entry (case-insensitive substring)
  not_blocklisted : symbol not in excluded_symbols
  not_cooled_down : tickers.cooldown_until is NULL or < asof
  regime          : if require_positive_regime: gex_profile.regime == 'positive'
                    else always True

passed = all(checks.values()). Pure function + DB reads only.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from ..data.prices import sma
from ..models import FilterReport, GexProfile, OptionQuote


def run_filters(symbol: str, cfg: dict, conn: sqlite3.Connection, *,
                spot: float, quotes: list[OptionQuote], closes: list[float],
                gex_profile: GexProfile, asof: date,
                iv_rank_val: float | None, vrp_val: float | None) -> FilterReport:
    """Stage-2 hard gate. All checks must pass; first failure does NOT short-circuit
    so the caller gets the full picture for logging/Discord notes."""
    f = cfg["filters"]
    checks: dict[str, bool] = {}
    values: dict[str, object] = {}

    # --- price_range ---
    checks["price_range"] = f["price_min"] <= spot <= f["price_max"]
    values["spot"] = spot

    # --- above_50dma ---
    if f.get("require_above_50dma", True):
        try:
            ma50 = sma(closes, 50)
            checks["above_50dma"] = spot > ma50
            values["sma50"] = round(ma50, 2)
        except ValueError:
            checks["above_50dma"] = False
            values["sma50"] = None
    else:
        checks["above_50dma"] = True
        values["sma50"] = None

    # --- open_interest: sum OI on 3 nearest strikes, nearest expiry > 7 DTE ---
    eligible_expiries = sorted(
        {q.expiry for q in quotes if (q.expiry - asof).days > 7},
        key=lambda e: (e - asof).days
    )
    if eligible_expiries:
        near_exp = eligible_expiries[0]
        near_quotes = [q for q in quotes if q.expiry == near_exp]
        strikes_sorted = sorted({q.strike for q in near_quotes}, key=lambda s: abs(s - spot))
        top3_strikes = set(strikes_sorted[:3])
        total_oi = sum(q.open_interest for q in near_quotes if q.strike in top3_strikes)
    else:
        total_oi = 0
    checks["open_interest"] = total_oi >= f["min_open_interest"]
    values["near_oi"] = total_oi

    # --- spread: ATM call on nearest expiry > 7 DTE ---
    atm_call = None
    if eligible_expiries:
        near_calls = [q for q in quotes if q.expiry == eligible_expiries[0] and q.kind == "C"]
        if near_calls:
            atm_call = min(near_calls, key=lambda q: abs(q.strike - spot))
    if atm_call is None:
        checks["spread"] = False
        values["spread"] = "no_quote"
    elif atm_call.bid == 0 and atm_call.ask == 0:
        checks["spread"] = False
        values["spread"] = "no_quote"
    else:
        sp = atm_call.spread_pct
        checks["spread"] = sp <= f["max_spread_pct"]
        values["spread"] = round(sp, 4)

    # --- iv_rank ---
    checks["iv_rank"] = iv_rank_val is not None and iv_rank_val >= f["min_iv_rank"]
    values["iv_rank"] = iv_rank_val

    # --- vrp ---
    checks["vrp"] = vrp_val is not None and vrp_val >= f["min_vrp"]
    values["vrp"] = vrp_val

    # --- earnings blackout ---
    row = conn.execute(
        "SELECT next_earnings_date FROM earnings WHERE symbol=?", (symbol,)
    ).fetchone()
    earnings_date = row["next_earnings_date"] if row else None
    if earnings_date and earnings_date != "unknown":
        try:
            from datetime import datetime
            ed = datetime.strptime(earnings_date, "%Y-%m-%d").date()
            blackout_end = asof + timedelta(days=f["earnings_blackout_days"])
            checks["earnings"] = ed > blackout_end
        except ValueError:
            checks["earnings"] = True  # unparseable -> treat as unknown -> pass
    else:
        checks["earnings"] = True  # None or 'unknown' -> pass
    values["earnings"] = earnings_date or "unknown"

    # --- sector exclusion (case-insensitive substring) ---
    ticker_row = conn.execute(
        "SELECT sector, excluded, exclusion_reason, cooldown_until FROM tickers WHERE symbol=?",
        (symbol,),
    ).fetchone()
    sym_sector = ticker_row["sector"] if ticker_row else None
    excluded_sectors = [s.lower() for s in f.get("excluded_sectors", [])]
    if sym_sector:
        sector_fail = any(excl in sym_sector.lower() for excl in excluded_sectors)
    else:
        sector_fail = False
    checks["sector"] = not sector_fail
    values["sector"] = sym_sector or "unknown"

    # --- not_blocklisted ---
    checks["not_blocklisted"] = symbol not in f.get("excluded_symbols", [])

    # --- not_cooled_down ---
    cooldown_until = ticker_row["cooldown_until"] if ticker_row else None
    if cooldown_until:
        try:
            from datetime import datetime
            cd_date = datetime.strptime(cooldown_until, "%Y-%m-%d").date()
            checks["not_cooled_down"] = asof > cd_date
        except ValueError:
            checks["not_cooled_down"] = True
    else:
        checks["not_cooled_down"] = True
    values["cooldown_until"] = cooldown_until

    # --- regime ---
    if f.get("require_positive_regime", False):
        checks["regime"] = gex_profile.regime == "positive"
    else:
        checks["regime"] = True
    values["regime"] = gex_profile.regime

    passed = all(checks.values())
    return FilterReport(symbol=symbol, passed=passed, checks=checks, values=values)
