"""Stage-2 gate acceptance tests."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from gexwheel import db as gdb
from gexwheel.analytics.gex import compute_profile
from gexwheel.models import GexProfile, OptionQuote
from gexwheel.screening.filters import run_filters

ASOF = date(2026, 6, 10)
EXP  = ASOF + timedelta(days=30)
SPOT = 20.0

BASE_CFG = {
    "filters": {
        "price_min": 5.0, "price_max": None,
        "min_open_interest": 500, "max_spread_pct": 0.10,
        "min_iv_rank": 50.0, "min_vrp": 0.0,
        "require_above_50dma": True,
        "earnings_blackout_days": 7,
        "excluded_sectors": ["Biotechnology"],
        "excluded_symbols": [],
        "require_positive_regime": False,
    }
}


def _conn(sector_val=None, cooldown=None):
    """In-memory DB with optional ticker row."""
    conn = gdb.connect(":memory:")
    if sector_val is not None:
        conn.execute(
            "INSERT INTO tickers(symbol, added_date, source, sector, excluded, cooldown_until)"
            " VALUES ('TEST', '2026-01-01', 'manual', ?, 0, ?)",
            (sector_val, cooldown),
        )
        conn.commit()
    return conn


def _quotes(spot=SPOT, oi=2000, bid=0.95, ask=1.00):
    """Minimal chain: one call + one put near spot."""
    return [
        OptionQuote("TEST", spot, EXP, "C", oi, 0.75, bid, ask),
        OptionQuote("TEST", spot, EXP, "P", oi, 0.75, bid, ask),
    ]


def _closes(spot=SPOT, above_50dma=True):
    """60+ closes with 50-day SMA below or above spot."""
    if above_50dma:
        # rising series ending at spot
        return [spot * (1 - 0.003 * (60 - i)) for i in range(61)]
    else:
        # falling series - spot below the SMA
        return [spot * (1 + 0.003 * (60 - i)) for i in range(61)]


def _profile(regime="positive"):
    return GexProfile(
        symbol="TEST", asof=ASOF, spot=SPOT,
        call_wall=25.0, put_wall=17.0, zero_gamma=20.0,
        net_gex=1e6 if regime == "positive" else -1e6,
        regime=regime,
    )


# ── tests ───────────────────────────────────────────────────────────────────

def test_all_checks_pass_for_clean_setup():
    report = run_filters(
        "TEST", BASE_CFG, _conn("Industrials"),
        spot=SPOT, quotes=_quotes(), closes=_closes(),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert report.passed, f"expected pass, checks={report.checks}"
    assert all(report.checks.values()), report.checks


def test_price_below_min_fails_only_price_check():
    report = run_filters(
        "TEST", BASE_CFG, _conn(),
        spot=4.0,           # below price_min=5
        quotes=_quotes(spot=4.0), closes=_closes(spot=4.0),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["price_range"] is False
    assert "iv_rank" in report.checks


def test_high_spot_passes_when_no_price_max():
    report = run_filters(
        "TEST", BASE_CFG, _conn(),
        spot=60.0,
        quotes=_quotes(spot=60.0), closes=_closes(spot=60.0),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert report.checks["price_range"] is True


def test_none_iv_rank_fails_gate_but_is_reported():
    report = run_filters(
        "TEST", BASE_CFG, _conn(),
        spot=SPOT, quotes=_quotes(), closes=_closes(),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=None, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["iv_rank"] is False
    assert report.values["iv_rank"] is None


def test_biotech_substring_exclusion():
    report = run_filters(
        "TEST", BASE_CFG, _conn("Biotechnology - Gene Editing"),
        spot=SPOT, quotes=_quotes(), closes=_closes(),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["sector"] is False


def test_no_quote_spread_fails():
    zero_quotes = [
        OptionQuote("TEST", SPOT, EXP, "C", 1000, 0.75, 0.0, 0.0),
        OptionQuote("TEST", SPOT, EXP, "P", 1000, 0.75, 0.0, 0.0),
    ]
    report = run_filters(
        "TEST", BASE_CFG, _conn(),
        spot=SPOT, quotes=zero_quotes, closes=_closes(),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["spread"] is False
    assert report.values["spread"] == "no_quote"


def test_earnings_blackout_fails():
    conn = _conn()
    conn.execute(
        "INSERT INTO earnings(symbol, next_earnings_date, updated_at) VALUES (?,?,?)",
        ("TEST", (ASOF + timedelta(days=3)).isoformat(), ASOF.isoformat()),
    )
    conn.commit()
    report = run_filters(
        "TEST", BASE_CFG, conn,
        spot=SPOT, quotes=_quotes(), closes=_closes(),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["earnings"] is False


def test_cooldown_ticker_fails():
    future_date = (ASOF + timedelta(days=3)).isoformat()
    report = run_filters(
        "TEST", BASE_CFG, _conn("Industrials", cooldown=future_date),
        spot=SPOT, quotes=_quotes(), closes=_closes(),
        gex_profile=_profile(), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["not_cooled_down"] is False


def test_regime_flag_respected():
    cfg_strict = {**BASE_CFG, "filters": {**BASE_CFG["filters"], "require_positive_regime": True}}
    report = run_filters(
        "TEST", cfg_strict, _conn(),
        spot=SPOT, quotes=_quotes(), closes=_closes(),
        gex_profile=_profile(regime="negative"), asof=ASOF,
        iv_rank_val=70.0, vrp_val=0.10,
    )
    assert not report.passed
    assert report.checks["regime"] is False
