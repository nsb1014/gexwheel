"""primary_watchlist + app_state DB helpers."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb

ASOF = date(2026, 6, 10)


def test_app_state_roundtrip():
    conn = gdb.connect(":memory:")
    assert gdb.get_app_state(conn, "last_screen_date") is None
    gdb.set_app_state(conn, "last_screen_date", ASOF.isoformat())
    assert gdb.get_app_state(conn, "last_screen_date") == ASOF.isoformat()
    # upsert overwrites
    gdb.set_app_state(conn, "last_screen_date", "2026-07-01")
    assert gdb.get_app_state(conn, "last_screen_date") == "2026-07-01"


def test_upsert_and_list_primary():
    conn = gdb.connect(":memory:")
    gdb.upsert_primary(conn, "AAA", ASOF, metrics={"spot": 20.0, "avg_volume": 2e6,
                       "near_oi": 5000, "spread": 0.02, "vrp": 0.3, "sector": "Tech"})
    gdb.upsert_primary(conn, "BBB", ASOF, metrics={"spot": 15.0})
    assert set(gdb.primary_symbols(conn)) == {"AAA", "BBB"}
    row = conn.execute("SELECT spot, avg_volume, sector FROM primary_watchlist WHERE symbol='AAA'").fetchone()
    assert row["spot"] == 20.0 and row["avg_volume"] == 2e6 and row["sector"] == "Tech"


def test_delete_primary():
    conn = gdb.connect(":memory:")
    gdb.upsert_primary(conn, "AAA", ASOF, metrics={"spot": 20.0})
    gdb.delete_primary(conn, "AAA")
    assert gdb.primary_symbols(conn) == []
