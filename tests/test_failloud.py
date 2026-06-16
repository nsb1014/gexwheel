"""Material data-outage failures must raise (so GitHub emails the operator)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.jobs import JobError
from gexwheel.jobs import screen as screen_job
from gexwheel.screening.discovery import run_discovery

ASOF = date(2026, 6, 10)


def test_discovery_raises_when_all_sources_fail():
    conn = gdb.connect(":memory:")
    gdb.upsert_primary(conn, "AAA", ASOF, metrics={"spot": 20.0})
    conn.commit()
    cfg = {
        "reddit": {"source": "apewisdom", "apewisdom_filter": "wsb", "apewisdom_pages": 1},
        "discovery": {"velocity_trigger": 3.0, "baseline_floor": 10,
                      "min_history_days": 5, "max_daily_mentions": 1000},
    }
    with patch("gexwheel.screening.discovery.fetch_apewisdom",
               side_effect=MentionFetchError("down")):
        with pytest.raises(MentionFetchError):
            run_discovery(conn, cfg, ASOF)


def test_screen_raises_on_universe_failure(tmp_path):
    cfg = {
        "db_path": str(tmp_path / "g.db"), "timezone": "America/New_York",
        "data": {"chain_source": "yfinance", "max_dte": 60, "request_sleep_s": 0, "request_retries": 1},
        "reddit": {"apewisdom_filter": "wsb"},
        "screen": {"primary_screen_interval_days": 21, "screen_pages": 5,
                   "avg_volume_days": 20, "min_avg_volume": 1_000_000},
        "filters": {"price_min": 10.0, "price_max": 45.0, "min_open_interest": 500,
                    "max_spread_pct": 0.10, "min_vrp": 0.0,
                    "excluded_sectors": [], "excluded_symbols": []},
    }
    conn = gdb.connect(cfg["db_path"])
    gdb.upsert_primary(conn, "KEEP", ASOF, metrics={"spot": 20.0})
    conn.commit()
    conn.close()
    with patch("gexwheel.jobs.screen.fetch_apewisdom", side_effect=MentionFetchError("down")):
        with pytest.raises(JobError):
            screen_job.run(cfg, force=True)
    # primary still intact (no destructive wipe before the raise)
    conn = gdb.connect(cfg["db_path"])
    assert gdb.primary_symbols(conn) == ["KEEP"]
