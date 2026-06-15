"""Periodic primary-watchlist screen.

run(cfg, *, force=False, asof=None) -> None
  1. asof = asof or today in cfg['timezone'].
  2. Self-throttle: if not force and (asof - last_screen_date) <
     primary_screen_interval_days, log and return.
  3. Universe = ApeWisdom (screen_pages) symbols UNION current primary members.
     If the universe pull fails entirely, log ERROR and ABORT without mutating
     the primary list (no destructive update on a transient API failure).
  4. Per symbol (try/except — one failure never kills the run): one chain fetch
     + one price fetch, run run_primary_screen, collect survivors.
  5. Replace primary watchlist with survivors: upsert survivors; delete prior
     members no longer surviving and demote them from the active watchlist.
  6. Persist last_screen_date=asof. Commit. One INFO summary line.

Logging: stdlib logging, INFO to stdout.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import db as gdb
from ..data.chains import make_chain_source
from ..data.mentions import MentionFetchError, fetch_apewisdom
from ..data.prices import daily_closes_and_volumes, sector
from ..screening.primary import run_primary_screen

log = logging.getLogger(__name__)


def run(cfg: dict, *, force: bool = False, asof=None) -> None:
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    if asof is None:
        asof = datetime.now(tz).date()
    s_cfg = cfg["screen"]
    interval = s_cfg.get("primary_screen_interval_days", 21)

    conn = gdb.connect(cfg["db_path"])
    try:
        last = gdb.get_app_state(conn, "last_screen_date")
        if not force and last:
            try:
                last_date = datetime.strptime(last, "%Y-%m-%d").date()
                if (asof - last_date).days < interval:
                    log.info("screen: not due (last=%s, interval=%dd) — skipping", last, interval)
                    return
            except ValueError:
                pass  # unparseable -> screen anyway

        # --- universe ---
        try:
            records = fetch_apewisdom(
                cfg["reddit"].get("apewisdom_filter", "wallstreetbets"),
                s_cfg.get("screen_pages", 5),
                asof,
            )
        except MentionFetchError as exc:
            log.error("screen: universe pull failed (%s) — aborting without changes", exc)
            return

        universe = {r.symbol for r in records} | set(gdb.primary_symbols(conn))
        if not universe:
            log.warning("screen: empty universe for %s — aborting", asof)
            return

        chain_src = make_chain_source(cfg)
        data_cfg = cfg["data"]
        survivors: set[str] = set()

        for symbol in sorted(universe):
            try:
                spot, quotes = chain_src.fetch(symbol, asof, data_cfg["max_dte"])
                closes, volumes = daily_closes_and_volumes(symbol)
                try:
                    sec = sector(symbol)
                except Exception:
                    sec = None
                report = run_primary_screen(
                    symbol, cfg, spot=spot, quotes=quotes, closes=closes,
                    volumes=volumes, asof=asof, sector=sec,
                )
                if report.passed:
                    survivors.add(symbol)
                    gdb.upsert_primary(conn, symbol, asof, metrics=report.values)
            except Exception as exc:
                log.error("screen: error for %s: %s", symbol, exc, exc_info=True)

        # --- demote prior members that did not survive ---
        prior = set(gdb.primary_symbols(conn))
        dropped = prior - survivors
        for symbol in dropped:
            gdb.delete_primary(conn, symbol)
            conn.execute(
                "UPDATE watchlist SET status='removed', notes=? WHERE symbol=? AND status!='removed'",
                ("dropped from primary screen", symbol),
            )

        gdb.set_app_state(conn, "last_screen_date", asof.isoformat())
        conn.commit()
        log.info("screen: %d survivors, %d dropped (universe=%d) for %s",
                 len(survivors), len(dropped), len(universe), asof)
    finally:
        conn.close()
