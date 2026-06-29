"""Periodic structural screen -> primary watchlist.

Pure function (no network, no DB) so it is unit-testable with synthetic data,
mirroring screening.filters. Reuses shared chain-metric helpers and the vol
module. iv_rank is intentionally NOT screened here (it needs per-symbol history
that cannot exist for a fresh universe); volatility is gated on vrp instead.
See docs/superpowers/specs/2026-06-15-screening-inversion-design.md.
"""
from __future__ import annotations

from datetime import date

from ..analytics.vol import atm_iv, realized_vol
from ..data.prices import avg_volume
from ..models import OptionQuote, PrimaryScreenReport
from .chain_metrics import atm_call_spread, near_oi_sum
from .filters import check_price_range


def run_primary_screen(symbol: str, cfg: dict, *, spot: float,
                       quotes: list[OptionQuote], closes: list[float],
                       volumes: list[float], asof: date,
                       sector: str | None) -> PrimaryScreenReport:
    """All checks must pass; no short-circuit so callers see the full picture."""
    f = cfg["filters"]
    s = cfg.get("screen", {})
    checks: dict[str, bool] = {}
    values: dict[str, object] = {}

    # price_range (pipeline floor; no max by default — see web/UI-REQUIREMENTS.md)
    checks["price_range"] = check_price_range(spot, f["price_min"], f.get("price_max"))
    values["spot"] = spot

    # avg_volume (NEW gate)
    window = s.get("avg_volume_days", 20)
    min_vol = s.get("min_avg_volume", 0)
    try:
        av = avg_volume(volumes, window)
        checks["avg_volume"] = av >= min_vol
        values["avg_volume"] = round(av, 1)
    except ValueError:
        checks["avg_volume"] = False
        values["avg_volume"] = None

    # optionable: OI on 3 nearest strikes
    oi = near_oi_sum(quotes, spot, asof)
    checks["optionable_oi"] = oi >= f["min_open_interest"]
    values["near_oi"] = oi

    # optionable: ATM call spread
    sp, status = atm_call_spread(quotes, spot, asof)
    if status == "no_quote":
        checks["optionable_spread"] = False
        values["spread"] = "no_quote"
    else:
        checks["optionable_spread"] = sp <= f["max_spread_pct"]
        values["spread"] = round(sp, 4)

    # volatility: vrp = atm_iv - realized_vol
    iv = atm_iv(quotes, spot, asof)
    try:
        rv = realized_vol(closes)
    except ValueError:
        rv = None
    vrp = (iv - rv) if (iv is not None and rv is not None) else None
    checks["volatility_vrp"] = vrp is not None and vrp >= f["min_vrp"]
    values["vrp"] = round(vrp, 4) if vrp is not None else None

    # sector exclusion (case-insensitive substring)
    excluded_sectors = [x.lower() for x in f.get("excluded_sectors", [])]
    if sector:
        sector_fail = any(excl in sector.lower() for excl in excluded_sectors)
    else:
        sector_fail = False
    checks["sector"] = not sector_fail
    values["sector"] = sector or "unknown"

    # blocklist
    checks["not_blocklisted"] = symbol not in f.get("excluded_symbols", [])

    passed = all(checks.values())
    return PrimaryScreenReport(symbol=symbol, passed=passed, checks=checks, values=values)
