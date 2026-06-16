"""Active-watchlist removal keys off the still-daily checks only."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb
from gexwheel.jobs.morning import _update_watchlist_membership
from gexwheel.models import FilterReport

ASOF = date(2026, 6, 15)


def _conn_with_active_watchlist(symbol: str = "TEST"):
    conn = gdb.connect(":memory:")
    conn.execute(
        "INSERT INTO tickers(symbol, added_date, source, excluded) VALUES (?, ?, 'manual', 0)",
        (symbol, "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO watchlist(symbol, date_added, status) VALUES (?, ?, 'active')",
        (symbol, "2026-01-01"),
    )
    conn.commit()
    return conn


def _report(**overrides: bool) -> FilterReport:
    checks = {
        "price_range": True, "open_interest": True, "iv_rank": True, "vrp": True,
        "spread": True, "above_50dma": True, "earnings": True, "sector": True,
        "not_blocklisted": True, "not_cooled_down": True, "regime": True,
    }
    checks.update(overrides)
    return FilterReport("TEST", all(checks.values()), checks=checks, values={})


def _status_and_notes(conn):
    row = conn.execute("SELECT status, notes FROM watchlist WHERE symbol='TEST'").fetchone()
    return row["status"], row["notes"]


def test_above_50dma_failure_removes_name():
    conn = _conn_with_active_watchlist()
    _update_watchlist_membership("TEST", _report(above_50dma=False), conn, ASOF)
    assert _status_and_notes(conn) == ("removed", "daily fail: above_50dma")


def test_earnings_failure_removes_name():
    conn = _conn_with_active_watchlist()
    _update_watchlist_membership("TEST", _report(earnings=False), conn, ASOF)
    assert _status_and_notes(conn) == ("removed", "daily fail: earnings")


def test_structural_failure_does_not_remove_active_name():
    # price/oi/spread/iv/vrp/sector are the SCREEN's job now, not a daily removal reason
    conn = _conn_with_active_watchlist()
    _update_watchlist_membership(
        "TEST", _report(open_interest=False, iv_rank=False, sector=False), conn, ASOF
    )
    assert _status_and_notes(conn) == ("active", None)
