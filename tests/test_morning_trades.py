"""Morning persists every identified trade (no Discord, no truncation)."""
from __future__ import annotations

from datetime import date

from gexwheel import db as gdb
from gexwheel.jobs.morning import _persist_trades
from gexwheel.models import AlertCard


def _card(symbol, score):
    return AlertCard(
        symbol=symbol, alert_type="put_wall_entry", spot=20.0, put_wall=18.0,
        call_wall=22.0, zero_gamma=20.0, regime="positive", iv_rank=70.0,
        vrp=0.1, score=score, suggested_entry="CSP 18P", notes="",
    )


def test_persist_trades_logs_all_with_timestamp():
    conn = gdb.connect(":memory:")
    asof = date(2026, 6, 15)
    cards = [_card("AAA", 90.0), _card("BBB", 80.0)]
    payloads = {
        ("AAA", "put_wall_entry"): {"spot": 20.0, "score": 90.0},
        ("BBB", "put_wall_entry"): {"spot": 20.0, "score": 80.0},
    }
    _persist_trades(conn, cards, payloads, asof, "2026-06-15T07:20:00")
    rows = conn.execute(
        "SELECT symbol, sent_at FROM alerts WHERE date=? ORDER BY symbol", (asof.isoformat(),)
    ).fetchall()
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
    assert all(r["sent_at"] == "2026-06-15T07:20:00" for r in rows)
