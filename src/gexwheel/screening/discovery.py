"""Stage 1: discovery.

run_discovery(conn, cfg, asof) -> list[VelocityResult]   (only triggered ones)

Steps:
  1. records = mentions.fetch_apewisdom(cfg reddit filter/pages, asof)
     - on MentionFetchError and cfg['reddit']['source'] allows it, try praw;
       if both fail, log ERROR and return [] (the job continues - GEX
       refresh of the existing watchlist must still run).
  2. db.record_mention() for every record, commit once.
  3. For each record: history = db.mention_history(days=8, source=<same>);
     drop today's row from history if present (compare iso date), pass the
     remaining counts (most recent first) to analytics.velocity.mention_velocity
     with cfg['discovery'] params.
  4. For triggered results: db.upsert_ticker(source='wsb_velocity').
     DO NOT add to watchlist here - Stage 2 owns that decision.
  5. Return triggered list sorted by ratio desc.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date

from .. import db
from ..analytics.velocity import mention_velocity
from ..data.mentions import MentionFetchError, fetch_apewisdom
from ..models import VelocityResult

log = logging.getLogger(__name__)


def run_discovery(conn: sqlite3.Connection, cfg: dict, asof: date) -> list[VelocityResult]:
    """Fetch mentions, persist them, compute velocity, return triggered results."""
    reddit_cfg = cfg["reddit"]
    disc_cfg = cfg["discovery"]

    # --- 1. Fetch mentions ---
    records = []
    source = reddit_cfg.get("source", "apewisdom")

    if source in ("apewisdom", "both"):
        try:
            records = fetch_apewisdom(
                reddit_cfg.get("apewisdom_filter", "wallstreetbets"),
                reddit_cfg.get("apewisdom_pages", 2),
                asof,
            )
        except MentionFetchError as exc:
            log.error("apewisdom fetch failed: %s", exc)

    if not records and source in ("praw", "both"):
        try:
            from ..data.mentions import fetch_praw
            records = fetch_praw(cfg, asof)
        except Exception as exc:
            log.error("praw fetch also failed: %s", exc)
            return []

    if not records:
        log.warning("discovery: no mention records retrieved for %s, skipping velocity", asof)
        return []

    # --- 2. Persist all mention records ---
    for rec in records:
        db.record_mention(conn, rec)
    conn.commit()
    log.info("discovery: persisted %d mention records for %s", len(records), asof)

    # --- 3. Compute velocity per ticker ---
    triggered: list[VelocityResult] = []
    today_iso = asof.isoformat()

    for rec in records:
        history_rows = db.mention_history(conn, rec.symbol, days=8, source=rec.source)
        # exclude today's row (it was just inserted) so baseline is prior days only
        prior_counts = [cnt for iso, cnt in history_rows if iso != today_iso]

        result = mention_velocity(
            rec.symbol,
            rec.mentions,
            prior_counts,
            trigger=disc_cfg.get("velocity_trigger", 3.0),
            baseline_floor=disc_cfg.get("baseline_floor", 10),
            min_history_days=disc_cfg.get("min_history_days", 5),
            max_daily_mentions=disc_cfg.get("max_daily_mentions", 1000),
        )

        if result.triggered:
            db.upsert_ticker(conn, rec.symbol, source="wsb_velocity", asof=asof)
            triggered.append(result)

    conn.commit()
    triggered.sort(key=lambda r: r.ratio, reverse=True)
    log.info("discovery: %d velocity triggers from %d tickers: %s",
             len(triggered), len(records),
             ", ".join(f"{r.symbol}({r.ratio:.1f}x)" for r in triggered[:8]))
    return triggered
