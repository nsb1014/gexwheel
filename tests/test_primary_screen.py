"""Primary structural screen (pure function)."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel.models import OptionQuote
from gexwheel.screening.primary import run_primary_screen

ASOF = date(2026, 6, 10)
EXP = ASOF + timedelta(days=30)
SPOT = 20.0

CFG = {
    "filters": {
        "price_min": 10.0, "price_max": 45.0,
        "min_open_interest": 500, "max_spread_pct": 0.10,
        "min_vrp": 0.0,
        "excluded_sectors": ["Biotechnology"],
        "excluded_symbols": [],
    },
    "screen": {"avg_volume_days": 20, "min_avg_volume": 1_000_000},
}


def _quotes(oi=2000, bid=0.95, ask=1.00):
    # high IV (0.80) so vrp = iv - rv is comfortably positive
    return [
        OptionQuote("TEST", SPOT, EXP, "C", oi, 0.80, bid, ask),
        OptionQuote("TEST", SPOT, EXP, "P", oi, 0.80, bid, ask),
    ]


def _closes():
    # ~flat series -> low realized vol so vrp stays positive
    return [SPOT for _ in range(61)]


def _volumes(v=2_000_000):
    return [float(v) for _ in range(61)]


def test_clean_setup_passes_all():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(), asof=ASOF, sector="Industrials",
    )
    assert rep.passed, rep.checks
    assert set(rep.checks) == {
        "price_range", "avg_volume", "optionable_oi",
        "optionable_spread", "volatility_vrp", "sector", "not_blocklisted",
    }


def test_low_volume_fails_only_volume():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(100_000), asof=ASOF, sector="Industrials",
    )
    assert not rep.passed
    assert rep.checks["avg_volume"] is False
    assert rep.checks["price_range"] is True


def test_biotech_sector_substring_fails():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(), asof=ASOF, sector="Biotechnology - Gene Editing",
    )
    assert not rep.passed
    assert rep.checks["sector"] is False


def test_no_quote_spread_fails():
    rep = run_primary_screen(
        "TEST", CFG, spot=SPOT, quotes=_quotes(bid=0.0, ask=0.0),
        closes=_closes(), volumes=_volumes(), asof=ASOF, sector="Industrials",
    )
    assert not rep.passed
    assert rep.checks["optionable_spread"] is False
    assert rep.values["spread"] == "no_quote"


def test_blocklisted_symbol_fails():
    cfg = {**CFG, "filters": {**CFG["filters"], "excluded_symbols": ["TEST"]}}
    rep = run_primary_screen(
        "TEST", cfg, spot=SPOT, quotes=_quotes(), closes=_closes(),
        volumes=_volumes(), asof=ASOF, sector="Industrials",
    )
    assert not rep.passed
    assert rep.checks["not_blocklisted"] is False
