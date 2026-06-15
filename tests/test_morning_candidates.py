"""Morning candidates are the active watchlist only (no discovery union)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from gexwheel import db as gdb
from gexwheel.jobs import morning as morning_job


def _base_cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "g.db"),
        "timezone": "America/New_York",
        "data": {"chain_source": "yfinance", "max_dte": 60,
                 "request_sleep_s": 0, "request_retries": 1, "risk_free_rate": 0.045},
        "reddit": {"source": "apewisdom"},
        "filters": {}, "alerts": {"cooldown_days": 5},
        "discord": {"max_alerts_per_run": 8, "webhook_url": "x"},
    }


def test_morning_candidates_are_active_watchlist_only(tmp_path):
    cfg = _base_cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    # an active watchlist name
    gdb.watchlist_add(conn, "ACTIVE", date(2026, 6, 10))
    # a discovery ticker NOT on the watchlist (old code would have included it)
    gdb.upsert_ticker(conn, "DISCO", source="wsb_velocity", asof=date(2026, 6, 10))
    conn.commit()
    conn.close()

    processed = []

    def _fake_process(symbol, *a, **k):
        processed.append(symbol)

    with patch("gexwheel.jobs.morning.make_chain_source"), \
         patch("gexwheel.jobs.morning._process_symbol", side_effect=_fake_process):
        morning_job.run(cfg)

    assert processed == ["ACTIVE"]
