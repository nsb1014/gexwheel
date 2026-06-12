"""Stage-1 discovery fallback behavior."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.models import MentionRecord
from gexwheel.screening.discovery import run_discovery


def test_run_discovery_falls_back_to_praw_when_both_allows_it():
    asof = date(2026, 6, 10)
    conn = gdb.connect(":memory:")
    for days_ago in range(1, 6):
        gdb.record_mention(
            conn,
            MentionRecord(
                symbol="TEST",
                asof=asof - timedelta(days=days_ago),
                mentions=10,
                source="praw",
            ),
        )
    conn.commit()
    cfg = {
        "reddit": {"source": "both", "apewisdom_filter": "wallstreetbets", "apewisdom_pages": 1},
        "discovery": {
            "velocity_trigger": 3.0,
            "baseline_floor": 10,
            "min_history_days": 5,
            "max_daily_mentions": 1000,
        },
    }

    with patch(
        "gexwheel.screening.discovery.fetch_apewisdom",
        side_effect=MentionFetchError("down"),
    ), patch(
        "gexwheel.data.mentions.fetch_praw",
        return_value=[MentionRecord("TEST", asof, 50, source="praw")],
    ):
        triggered = run_discovery(conn, cfg, asof)

    assert [r.symbol for r in triggered] == ["TEST"]
    row = conn.execute(
        "SELECT mentions FROM mentions WHERE symbol='TEST' AND date=? AND source='praw'",
        (asof.isoformat(),),
    ).fetchone()
    assert row["mentions"] == 50
