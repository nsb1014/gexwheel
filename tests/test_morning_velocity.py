"""Alert-velocity ratio must never mix mention sources."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel import db as gdb
from gexwheel.jobs.morning import _velocity_ratio

ASOF = date(2026, 6, 10)


def _seed(conn, source: str, day: date, mentions: int) -> None:
    conn.execute(
        "INSERT INTO mentions(symbol, date, source, mentions) VALUES (?,?,?,?)",
        ("TEST", day.isoformat(), source, mentions),
    )


def test_velocity_ratio_uses_only_requested_source():
    conn = gdb.connect(":memory:")
    # apewisdom: flat 10/day baseline, 30 today -> ratio 3.0
    for i in range(1, 8):
        _seed(conn, "apewisdom", ASOF - timedelta(days=i), 10)
    _seed(conn, "apewisdom", ASOF, 30)
    # praw noise that would poison the baseline and today's count if mixed
    for i in range(1, 8):
        _seed(conn, "praw", ASOF - timedelta(days=i), 1000)
    _seed(conn, "praw", ASOF, 1)
    conn.commit()

    assert _velocity_ratio(conn, "TEST", ASOF, "apewisdom") == 3.0


def test_velocity_ratio_none_without_history():
    conn = gdb.connect(":memory:")
    _seed(conn, "apewisdom", ASOF, 30)
    conn.commit()

    assert _velocity_ratio(conn, "TEST", ASOF, "apewisdom") is None
