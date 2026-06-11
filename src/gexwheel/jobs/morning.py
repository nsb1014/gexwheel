"""Weekday pre-market job (~07:15 ET). TODO(sonnet): implement run().

run(cfg) -> None
  Candidates = union of:
    a) db.watchlist_active(conn)
    b) tickers rows where excluded=0 AND cooldown ok AND symbol not in
       watchlist  (i.e. fresh discovery output awaiting Stage-2 evaluation)

  Per symbol (wrap the WHOLE per-symbol block in try/except - one bad
  ticker must never kill the run; log ERROR with symbol and continue):
    1. spot, quotes = chain_source.fetch(symbol, asof, max_dte)
    2. closes = prices.daily_closes(symbol)
    3. profile = analytics.gex.compute_profile(...); db.record_gex(profile)
    4. iv = vol.atm_iv(quotes, spot, asof); rv = vol.realized_vol(closes)
       ivr = vol.iv_rank(iv, <trailing iv_atm from vol_stats table>)
       vrp = iv - rv if iv is not None else None; store row in vol_stats
    5. refresh earnings table if stale (> 7 days old)
    6. report = filters.run_filters(...)
       - candidate (b) + passed  -> db.watchlist_add()
       - watchlist member + FAILED on structural checks (price_range,
         sector, blocklist) -> set watchlist status='removed'
       - transient fails (iv_rank None, spread no_quote) -> keep, log
    7. Wall-break check: if profile.put_wall and spot < put_wall:
         db.bench_ticker(symbol, asof + cooldown_days, 'closed below put wall')
         continue
    8. if report.passed and scoring.should_alert(profile, cfg, conn, asof):
         build AlertCard (suggested_entry per scoring docstring), collect

  Finally: sent_cards = discord.send_alerts(cards, cfg); db.log_alert for each
  candidate card (sent_at = iso now only when that card was actually posted);
  commit; one INFO summary line; close.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import db as gdb
from ..alerts import discord as disc
from ..alerts.scoring import score, should_alert
from ..alerts.scoring import suggested_entry as make_entry
from ..analytics import gex as gex_mod
from ..analytics import vol as vol_mod
from ..data.chains import ChainFetchError, make_chain_source
from ..data.prices import PriceFetchError, daily_closes, next_earnings, sector
from ..models import AlertCard
from ..screening.filters import run_filters

log = logging.getLogger(__name__)


def run(cfg: dict) -> None:
    """Weekday pre-market: refresh GEX, run filters, fire Discord alerts."""
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    asof = datetime.now(tz).date()
    log.info("morning: starting for %s", asof)

    conn = gdb.connect(cfg["db_path"])
    chain_src = make_chain_source(cfg)
    data_cfg = cfg["data"]
    alert_cfg = cfg["alerts"]
    cooldown_days = alert_cfg.get("cooldown_days", 5)

    # Candidates = active watchlist + new discovery tickers not yet on watchlist
    watchlist = set(gdb.watchlist_active(conn))
    discovery_rows = conn.execute(
        """SELECT symbol FROM tickers
           WHERE excluded=0
           AND (cooldown_until IS NULL OR cooldown_until < ?)
           AND symbol NOT IN (SELECT symbol FROM watchlist)""",
        (asof.isoformat(),),
    ).fetchall()
    candidates = list(watchlist) + [r["symbol"] for r in discovery_rows]
    log.info("morning: %d candidates (%d watchlist, %d new discovery)",
             len(candidates), len(watchlist), len(discovery_rows))

    cards: list[AlertCard] = []

    for symbol in candidates:
        try:
            _process_symbol(
                symbol, asof, conn, chain_src, cfg, data_cfg,
                cooldown_days, watchlist, cards
            )
        except Exception as exc:
            log.error("morning: unhandled error for %s: %s", symbol, exc, exc_info=True)

    # Send alerts
    if cards:
        max_cards = cfg["discord"]["max_alerts_per_run"]
        top_cards = sorted(cards, key=lambda c: c.score, reverse=True)[:max_cards]
        sent_cards = disc.send_alerts(cards, cfg)
        sent_keys = {(c.symbol, c.alert_type) for c in sent_cards}
        now_iso = datetime.now(tz).isoformat()
        for card in top_cards:
            delivered = (card.symbol, card.alert_type) in sent_keys
            gdb.log_alert(conn, card.symbol, asof, card.alert_type,
                          {"spot": card.spot, "put_wall": card.put_wall,
                           "score": card.score, "suggested": card.suggested_entry},
                          now_iso if delivered else None)
        conn.commit()
        log.info("morning: sent %d/%d alerts", len(sent_cards), len(top_cards))
    else:
        log.info("morning: no alerts generated")

    conn.commit()
    conn.close()


def _process_symbol(symbol, asof, conn, chain_src, cfg, data_cfg,
                    cooldown_days, watchlist, cards):
    """Per-symbol processing block. All exceptions bubble to the caller's handler."""

    # 1. Fetch chain
    spot, quotes = chain_src.fetch(symbol, asof, data_cfg["max_dte"])

    # 2. Fetch closes
    closes = daily_closes(symbol)

    # 3. GEX profile
    profile = gex_mod.compute_profile(
        symbol, quotes, spot, asof,
        r=data_cfg.get("risk_free_rate", 0.045),
        max_dte=data_cfg["max_dte"],
    )
    gdb.record_gex(conn, profile)

    # 4. Vol stats
    try:
        rv = vol_mod.realized_vol(closes)
    except ValueError:
        rv = None

    iv = vol_mod.atm_iv(quotes, spot, asof)

    # Pull iv history from vol_stats
    iv_history_rows = conn.execute(
        "SELECT iv_atm FROM vol_stats WHERE symbol=? AND iv_atm IS NOT NULL ORDER BY date DESC LIMIT 252",
        (symbol,),
    ).fetchall()
    iv_history = [r["iv_atm"] for r in iv_history_rows]

    ivr = vol_mod.iv_rank(iv, iv_history) if iv is not None else None
    vrp = (iv - rv) if (iv is not None and rv is not None) else None

    conn.execute(
        """INSERT INTO vol_stats(symbol, date, iv_atm, iv_rank, rv20, vrp)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(symbol,date) DO UPDATE SET
             iv_atm=excluded.iv_atm, iv_rank=excluded.iv_rank,
             rv20=excluded.rv20, vrp=excluded.vrp""",
        (symbol, asof.isoformat(), iv, ivr, rv, vrp),
    )

    # 5. Refresh earnings + sector if stale
    _refresh_earnings(conn, symbol, asof)
    _refresh_sector(conn, symbol)

    # 6. Run Stage-2 filters
    report = run_filters(
        symbol, cfg, conn,
        spot=spot, quotes=quotes, closes=closes,
        gex_profile=profile, asof=asof,
        iv_rank_val=ivr, vrp_val=vrp,
    )
    log.info("morning: %s filters passed=%s checks=%s", symbol, report.passed, report.checks)

    # Promote/demote watchlist membership
    if symbol not in watchlist and report.passed:
        gdb.watchlist_add(conn, symbol, asof, score=None)
    elif symbol in watchlist:
        structural_fail = any(
            not report.checks.get(k, True)
            for k in ("price_range", "sector", "not_blocklisted")
        )
        if structural_fail:
            conn.execute("UPDATE watchlist SET status='removed' WHERE symbol=?", (symbol,))
            log.info("morning: %s removed from watchlist (structural fail)", symbol)

    # 7. Wall-break check
    if profile.put_wall is not None and spot < profile.put_wall:
        bench_until = asof + timedelta(days=cooldown_days)
        gdb.bench_ticker(conn, symbol, bench_until, "closed below put wall")
        log.warning("morning: %s benched until %s (below put wall %.2f)", symbol, bench_until, profile.put_wall)
        return

    # 8. Alert
    if report.passed and should_alert(profile, cfg, conn, asof):
        vel_row = conn.execute(
            """SELECT mentions, (SELECT AVG(mentions) FROM (
                   SELECT mentions FROM mentions WHERE symbol=? AND date < ?
                   ORDER BY date DESC LIMIT 7)) AS baseline
               FROM mentions WHERE symbol=? AND date=?""",
            (symbol, asof.isoformat(), symbol, asof.isoformat()),
        ).fetchone()
        vel_ratio = None
        if vel_row and vel_row["baseline"]:
            try:
                vel_ratio = vel_row["mentions"] / vel_row["baseline"]
            except (TypeError, ZeroDivisionError):
                pass

        card_score = score(profile, ivr, vrp, vel_ratio)
        card = AlertCard(
            symbol=symbol,
            alert_type="put_wall_entry",
            spot=spot,
            put_wall=profile.put_wall,
            call_wall=profile.call_wall,
            zero_gamma=profile.zero_gamma,
            regime=profile.regime,
            iv_rank=ivr,
            vrp=vrp,
            score=card_score,
            suggested_entry=make_entry(profile),
            notes="2d wall" if report.checks.get("regime") else "",
        )
        cards.append(card)
        log.info("morning: %s generated alert score=%.1f", symbol, card_score)


def _refresh_earnings(conn, symbol, asof):
    """Update earnings table if row is missing or > 7 days stale."""
    row = conn.execute(
        "SELECT next_earnings_date, updated_at FROM earnings WHERE symbol=?", (symbol,)
    ).fetchone()
    stale = True
    if row and row["updated_at"]:
        try:
            from datetime import datetime
            updated = datetime.strptime(row["updated_at"], "%Y-%m-%d").date()
            stale = (asof - updated).days > 7
        except ValueError:
            pass
    if stale:
        try:
            ed = next_earnings(symbol)
            conn.execute(
                """INSERT INTO earnings(symbol, next_earnings_date, updated_at)
                   VALUES (?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     next_earnings_date=excluded.next_earnings_date,
                     updated_at=excluded.updated_at""",
                (symbol, ed.isoformat() if ed else None, asof.isoformat()),
            )
        except Exception as exc:
            log.debug("earnings refresh failed for %s: %s", symbol, exc)


def _refresh_sector(conn, symbol):
    """Populate sector in tickers table if missing (slow yfinance .info call)."""
    row = conn.execute("SELECT sector FROM tickers WHERE symbol=?", (symbol,)).fetchone()
    if row and row["sector"] is None:
        try:
            sec = sector(symbol)
            if sec:
                conn.execute("UPDATE tickers SET sector=? WHERE symbol=?", (sec, symbol))
        except Exception as exc:
            log.debug("sector refresh failed for %s: %s", symbol, exc)
