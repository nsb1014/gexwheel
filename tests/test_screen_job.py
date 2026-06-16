"""screen job: throttling, survivor persistence, incumbent demotion, safe-abort."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError
from gexwheel.jobs import screen as screen_job
from gexwheel.models import MentionRecord, OptionQuote

ASOF = date(2026, 6, 10)
EXP = ASOF + timedelta(days=30)


def _cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "g.db"),
        "timezone": "America/New_York",
        "data": {"chain_source": "yfinance", "max_dte": 60,
                 "request_sleep_s": 0, "request_retries": 1},
        "reddit": {"source": "apewisdom", "apewisdom_filter": "wallstreetbets",
                   "apewisdom_pages": 2},
        "screen": {"primary_screen_interval_days": 21, "screen_pages": 5,
                   "avg_volume_days": 20, "min_avg_volume": 1_000_000},
        "filters": {"price_min": 10.0, "price_max": 45.0, "min_open_interest": 500,
                    "max_spread_pct": 0.10, "min_vrp": 0.0,
                    "excluded_sectors": ["Biotechnology"], "excluded_symbols": []},
    }


def _good_chain(symbol="AAA", spot=20.0):
    quotes = [
        OptionQuote(symbol, spot, EXP, "C", 2000, 0.80, 0.95, 1.00),
        OptionQuote(symbol, spot, EXP, "P", 2000, 0.80, 0.95, 1.00),
    ]
    return spot, quotes


class _FakeChain:
    def fetch(self, symbol, asof, max_dte):
        return _good_chain(symbol)


def _patches(universe_symbols, prices_ok=True):
    """Patch the external IO the screen job depends on."""
    records = [MentionRecord(s, ASOF, 100, source="apewisdom") for s in universe_symbols]
    closes = [20.0] * 61
    volumes = [2_000_000.0] * 61
    return (
        patch("gexwheel.jobs.screen.fetch_apewisdom", return_value=records),
        patch("gexwheel.jobs.screen.make_chain_source", return_value=_FakeChain()),
        patch("gexwheel.jobs.screen.daily_closes_and_volumes",
              return_value=(closes, volumes)),
        patch("gexwheel.jobs.screen.sector", return_value="Industrials"),
    )


def test_screen_persists_survivors(tmp_path):
    cfg = _cfg(tmp_path)
    p1, p2, p3, p4 = _patches(["AAA", "BBB"])
    with p1, p2, p3, p4:
        screen_job.run(cfg, force=True, asof=ASOF)
    conn = gdb.connect(cfg["db_path"])
    assert set(gdb.primary_symbols(conn)) == {"AAA", "BBB"}
    assert gdb.get_app_state(conn, "last_screen_date") == ASOF.isoformat()


def test_screen_not_due_is_noop(tmp_path):
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    gdb.set_app_state(conn, "last_screen_date", ASOF.isoformat())
    conn.commit()
    conn.close()
    p1, p2, p3, p4 = _patches(["AAA"])
    with p1 as m_fetch, p2, p3, p4:
        # asof passed explicitly so "now" is deterministic; 5 days later < 21
        screen_job.run(cfg, force=False, asof=ASOF + timedelta(days=5))
        m_fetch.assert_not_called()


def test_screen_demotes_incumbent_that_now_fails_the_screen(tmp_path):
    # Incumbents are always re-screened (universe = apewisdom UNION incumbents),
    # so a name drops only when it FAILS the screen now — here OLD fails the
    # avg-volume gate while AAA passes.
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    gdb.upsert_primary(conn, "OLD", ASOF - timedelta(days=21), metrics={"spot": 20.0})
    gdb.watchlist_add(conn, "OLD", ASOF - timedelta(days=21))
    conn.commit()
    conn.close()

    closes = [20.0] * 61

    def _vols(symbol, lookback_days=120):
        v = 100_000.0 if symbol == "OLD" else 2_000_000.0   # OLD below min_avg_volume
        return closes, [v] * 61

    records = [MentionRecord("AAA", ASOF, 100, source="apewisdom")]
    with patch("gexwheel.jobs.screen.fetch_apewisdom", return_value=records), \
         patch("gexwheel.jobs.screen.make_chain_source", return_value=_FakeChain()), \
         patch("gexwheel.jobs.screen.daily_closes_and_volumes", side_effect=_vols), \
         patch("gexwheel.jobs.screen.sector", return_value="Industrials"):
        screen_job.run(cfg, force=True)

    conn = gdb.connect(cfg["db_path"])
    assert "OLD" not in gdb.primary_symbols(conn)
    assert "AAA" in gdb.primary_symbols(conn)
    status = conn.execute("SELECT status FROM watchlist WHERE symbol='OLD'").fetchone()["status"]
    assert status == "removed"


def test_screen_aborts_without_wiping_on_fetch_failure(tmp_path):
    from gexwheel.jobs import JobError
    cfg = _cfg(tmp_path)
    conn = gdb.connect(cfg["db_path"])
    gdb.upsert_primary(conn, "KEEP", ASOF - timedelta(days=21), metrics={"spot": 20.0})
    conn.commit()
    conn.close()
    with patch("gexwheel.jobs.screen.fetch_apewisdom",
               side_effect=MentionFetchError("down")):
        with pytest.raises(JobError):
            screen_job.run(cfg, force=True)
    conn = gdb.connect(cfg["db_path"])
    assert gdb.primary_symbols(conn) == ["KEEP"]  # untouched
