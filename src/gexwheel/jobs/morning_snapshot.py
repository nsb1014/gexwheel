"""Morning spot₀ snapshot (~10:15 ET).

Records equity-only prices for the active watchlist before the 10:45 identify
run pulls option chains. Used to invalidate trades when spot moves too far
relative to implied move between the two snapshots.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import db as gdb
from ..data.prices import PriceFetchError, current_spot

log = logging.getLogger(__name__)


def run(cfg: dict) -> None:
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    asof = datetime.now(tz).date()
    now_iso = datetime.now(tz).isoformat()
    log.info("morning-snapshot: starting spot0 for %s", asof)

    conn = gdb.connect(cfg["db_path"])
    symbols = gdb.watchlist_active(conn)
    log.info("morning-snapshot: %d active watchlist symbols", len(symbols))

    spots: dict[str, float] = {}
    for symbol in symbols:
        try:
            spots[symbol] = current_spot(symbol)
        except PriceFetchError as exc:
            log.error("morning-snapshot: %s spot0 failed: %s", symbol, exc)

    gdb.set_morning_spot0(conn, asof, spots, now_iso)
    conn.commit()
    conn.close()
    log.info("morning-snapshot: stored spot0 for %d/%d symbols", len(spots), len(symbols))
