"""Daily Reddit scan job - runs EVERY day incl. weekends (baselines need
continuous history).

run(cfg) -> None
  1. asof = today in cfg['timezone'] (zoneinfo, not naive date.today()).
  2. conn = db.connect(cfg['db_path'])
  3. triggered = screening.discovery.run_discovery(conn, cfg, asof)
  4. Log a one-line summary: '<n> tickers scanned, <k> velocity triggers: A, B, C'
  5. Weekends/holidays: that's it - Stage 2 needs market data and runs in
     the morning job. Persist triggers via the tickers table (discovery
     already upserted them with source='wsb_velocity'); morning job picks
     up any non-excluded ticker with no watchlist row for evaluation.
  6. conn.commit(); conn.close(). Exit code 0 unless db itself failed -
     systemd treats nonzero as failure and journald captures the trace.

Logging: stdlib logging, INFO to stdout (journald picks it up from podman).
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from datetime import datetime

from .. import db
from ..config import load_config
from ..screening.discovery import run_discovery

log = logging.getLogger(__name__)


def run(cfg: dict) -> None:
    """Daily Reddit scan job - runs every day including weekends."""
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    asof = datetime.now(tz).date()

    log.info("mentions_daily: starting for %s", asof)
    conn = db.connect(cfg["db_path"])
    try:
        triggered = run_discovery(conn, cfg, asof)
        symbols = [r.symbol for r in triggered]
        log.info(
            "mentions_daily: complete — %d triggers%s",
            len(triggered),
            f": {', '.join(symbols)}" if symbols else "",
        )
    finally:
        conn.close()
