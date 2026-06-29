"""morning_spot0 app_state helpers."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb

ASOF = date(2026, 6, 16)


def test_morning_spot0_roundtrip():
    conn = gdb.connect(":memory:")
    gdb.set_morning_spot0(conn, ASOF, {"AAA": 20.5, "BBB": 15.0}, "2026-06-16T10:15:00-04:00")
    conn.commit()
    assert gdb.get_morning_spot0(conn, ASOF) == {"AAA": 20.5, "BBB": 15.0}
    assert gdb.get_morning_spot0(conn, date(2026, 6, 15)) == {}
