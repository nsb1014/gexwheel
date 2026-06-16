"""Stage-1 discovery: primary-member narrowing, promotion, praw fallback."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.models import MentionRecord
from gexwheel.screening.discovery import run_discovery

ASOF = date(2026, 6, 10)

CFG = {
    "reddit": {"source": "both", "apewisdom_filter": "wallstreetbets", "apewisdom_pages": 1},
    "discovery": {
        "velocity_trigger": 3.0, "baseline_floor": 10,
        "min_history_days": 5, "max_daily_mentions": 1000,
    },
}


def _seed_primary(conn, *symbols):
    for s in symbols:
        gdb.upsert_primary(conn, s, ASOF, metrics={"spot": 20.0})
    conn.commit()


def test_discovery_skips_when_primary_empty():
    conn = gdb.connect(":memory:")
    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        return_value=[MentionRecord("AAA", ASOF, 100, source="apewisdom")],
    ):
        assert run_discovery(conn, CFG, ASOF) == []
    # nothing persisted
    assert conn.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"] == 0


def test_discovery_drops_non_primary_symbols():
    conn = gdb.connect(":memory:")
    _seed_primary(conn, "AAA")
    # baseline history for AAA so it can trigger
    for d in range(1, 6):
        gdb.record_mention(conn, MentionRecord("AAA", ASOF - timedelta(days=d), 10, source="apewisdom"))
    conn.commit()
    records = [
        MentionRecord("AAA", ASOF, 50, source="apewisdom"),   # primary -> kept
        MentionRecord("ZZZ", ASOF, 9999, source="apewisdom"), # not primary -> dropped
    ]
    with patch("gexwheel.screening.discovery.fetch_apewisdom", return_value=records):
        triggered = run_discovery(conn, CFG, ASOF)
    assert [r.symbol for r in triggered] == ["AAA"]
    # ZZZ never persisted
    assert conn.execute("SELECT COUNT(*) c FROM mentions WHERE symbol='ZZZ'").fetchone()["c"] == 0


def test_triggered_primary_name_is_promoted_to_active_watchlist():
    conn = gdb.connect(":memory:")
    _seed_primary(conn, "AAA")
    for d in range(1, 6):
        gdb.record_mention(conn, MentionRecord("AAA", ASOF - timedelta(days=d), 10, source="apewisdom"))
    conn.commit()
    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        return_value=[MentionRecord("AAA", ASOF, 50, source="apewisdom")],
    ):
        run_discovery(conn, CFG, ASOF)
    assert "AAA" in gdb.watchlist_active(conn)
    # tickers metadata row exists (so cooldown/bench works later)
    assert conn.execute("SELECT COUNT(*) c FROM tickers WHERE symbol='AAA'").fetchone()["c"] == 1


def test_discovery_falls_back_to_praw_for_primary_member():
    conn = gdb.connect(":memory:")
    _seed_primary(conn, "TEST")
    for d in range(1, 6):
        gdb.record_mention(conn, MentionRecord("TEST", ASOF - timedelta(days=d), 10, source="praw"))
    conn.commit()
    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        side_effect=MentionFetchError("down"),
    ), patch(
        "gexwheel.data.mentions.fetch_praw",
        return_value=[MentionRecord("TEST", ASOF, 50, source="praw")],
    ):
        triggered = run_discovery(conn, CFG, ASOF)
    assert [r.symbol for r in triggered] == ["TEST"]
    row = conn.execute(
        "SELECT mentions FROM mentions WHERE symbol='TEST' AND date=? AND source='praw'",
        (ASOF.isoformat(),),
    ).fetchone()
    assert row["mentions"] == 50
