"""Watchlist membership and weekly pruning behavior."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb
from gexwheel.jobs.morning import _update_watchlist_membership
from gexwheel.models import FilterReport


def _conn_with_active_watchlist(symbol: str = "TEST"):
    conn = gdb.connect(":memory:")
    conn.execute(
        "INSERT INTO tickers(symbol, added_date, source, excluded) VALUES (?, ?, ?, 0)",
        (symbol, "2026-01-01", "manual"),
    )
    conn.execute(
        "INSERT INTO watchlist(symbol, date_added, status) VALUES (?, ?, 'active')",
        (symbol, "2026-01-01"),
    )
    conn.commit()
    return conn


def _report(*, values: dict | None = None, **overrides: bool) -> FilterReport:
    checks = {
        "price_range": True,
        "open_interest": True,
        "iv_rank": True,
        "vrp": True,
        "spread": True,
        "earnings": True,
        "sector": True,
        "not_blocklisted": True,
        "not_cooled_down": True,
        "regime": True,
    }
    checks.update(overrides)
    return FilterReport("TEST", all(checks.values()), checks=checks, values=values or {})


def _status_and_notes(conn):
    row = conn.execute("SELECT status, notes FROM watchlist WHERE symbol='TEST'").fetchone()
    return row["status"], row["notes"]


def test_weekly_pruning_removes_watchlist_name_that_fails_open_interest():
    conn = _conn_with_active_watchlist()

    _update_watchlist_membership(
        "TEST", {"TEST"}, _report(open_interest=False), conn, date(2026, 6, 15)
    )

    assert _status_and_notes(conn) == ("removed", "weekly prune: open_interest")


def test_weekly_pruning_waits_until_week_start_for_volatility_failures():
    conn = _conn_with_active_watchlist()

    _update_watchlist_membership(
        "TEST", {"TEST"}, _report(iv_rank=False, vrp=False), conn, date(2026, 6, 16)
    )

    assert _status_and_notes(conn) == ("active", None)


def test_daily_structural_failure_still_removes_watchlist_name():
    conn = _conn_with_active_watchlist()

    _update_watchlist_membership(
        "TEST", {"TEST"}, _report(sector=False), conn, date(2026, 6, 16)
    )

    assert _status_and_notes(conn) == ("removed", "structural fail: sector")


def test_weekly_pruning_ignores_transient_quote_failures():
    conn = _conn_with_active_watchlist()

    _update_watchlist_membership(
        "TEST", {"TEST"}, _report(spread=False), conn, date(2026, 6, 15)
    )

    assert _status_and_notes(conn) == ("active", None)


def test_weekly_pruning_does_not_remove_when_no_quote_causes_durable_failures():
    conn = _conn_with_active_watchlist()

    _update_watchlist_membership(
        "TEST",
        {"TEST"},
        _report(
            spread=False,
            open_interest=False,
            iv_rank=False,
            vrp=False,
            values={"spread": "no_quote"},
        ),
        conn,
        date(2026, 6, 15),
    )

    assert _status_and_notes(conn) == ("active", None)
