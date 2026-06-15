"""Weekday pre-market job (~07:15 ET).

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
from ..analytics.gex import put_wall_strength
from ..data.chains import make_chain_source
from ..data.prices import daily_closes, next_earnings, sector
from ..models import AlertCard, FilterReport
from ..screening.filters import run_filters

log = logging.getLogger(__name__)

# Active names are demoted only on the still-daily checks. Structural gating
# (price/volume/oi/spread/vrp/sector/blocklist) lives in the periodic screen.
_DAILY_REMOVE_CHECKS = ("above_50dma", "earnings")


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

    # Candidates = the active (secondary) watchlist only. Structural entry-gating
    # now lives in the periodic screen (jobs/screen.py); discovery promotes names
    # onto this list via velocity. See specs/2026-06-15-screening-inversion-design.md.
    candidates = sorted(gdb.watchlist_active(conn))
    log.info("morning: %d active watchlist candidates", len(candidates))

    cards: list[AlertCard] = []
    alert_payloads: dict[tuple[str, str], dict] = {}

    for symbol in candidates:
        try:
            _process_symbol(
                symbol, asof, conn, chain_src, cfg, data_cfg,
                cooldown_days, cards, alert_payloads
            )
        except Exception as exc:
            log.error("morning: unhandled error for %s: %s", symbol, exc, exc_info=True)

    # Send alerts
    if cards:
        # send_alerts sorts and truncates to max_alerts_per_run internally;
        # truncate here too so only attempted cards are logged (re-sorting an
        # already-truncated list inside send_alerts is a no-op).
        max_cards = cfg["discord"].get("max_alerts_per_run", 8)
        top_cards = sorted(cards, key=lambda c: c.score, reverse=True)[:max_cards]
        sent_cards = disc.send_alerts(top_cards, cfg)
        sent_keys = {(c.symbol, c.alert_type) for c in sent_cards}
        now_iso = datetime.now(tz).isoformat()
        for card in top_cards:
            delivered = (card.symbol, card.alert_type) in sent_keys
            gdb.log_alert(
                conn, card.symbol, asof, card.alert_type,
                alert_payloads[(card.symbol, card.alert_type)],
                now_iso if delivered else None,
            )
        conn.commit()
        log.info("morning: sent %d/%d alerts", len(sent_cards), len(top_cards))
    else:
        log.info("morning: no alerts generated")

    conn.commit()
    conn.close()


def _process_symbol(symbol, asof, conn, chain_src, cfg, data_cfg,
                    cooldown_days, cards, alert_payloads):
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
    _update_watchlist_membership(symbol, report, conn, asof)

    # 7. Wall-break check
    if profile.put_wall is not None and spot < profile.put_wall:
        bench_until = asof + timedelta(days=cooldown_days)
        gdb.bench_ticker(conn, symbol, bench_until, "closed below put wall")
        log.warning("morning: %s benched until %s (below put wall %.2f)", symbol, bench_until, profile.put_wall)
        return

    # 8. Alert
    if report.passed and should_alert(profile, cfg, conn, asof):
        # NOTE: velocity context follows the configured discovery source;
        # 'both' falls back to apewisdom (the primary).
        mention_source = "praw" if cfg["reddit"].get("source") == "praw" else "apewisdom"
        vel_ratio = _velocity_ratio(conn, symbol, asof, mention_source)

        card_score = score(profile, ivr, vrp, vel_ratio)
        strength = put_wall_strength(profile)
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
            notes=_alert_notes(profile, report),
        )
        cards.append(card)
        alert_payloads[(card.symbol, card.alert_type)] = _alert_payload(
            card, put_wall_strength_val=strength
        )
        log.info("morning: %s generated alert score=%.1f", symbol, card_score)


def _update_watchlist_membership(symbol: str, watchlist: set[str], report: FilterReport,
                                 conn, asof) -> None:
    """Promote new passing names, remove active names that no longer meet durable gates."""
    if symbol not in watchlist:
        if report.passed:
            gdb.watchlist_add(conn, symbol, asof, score=None)
        return

    failures = _failed_checks(report, _DAILY_REMOVE_CHECKS)
    reason_prefix = "structural fail"

    if not failures and asof.weekday() == 0:
        if report.values.get("spread") == "no_quote":
            return
        failures = _failed_checks(report, _WEEKLY_PRUNE_CHECKS)
        reason_prefix = "weekly prune"

    if not failures:
        return

    note = f"{reason_prefix}: {', '.join(failures)}"
    conn.execute(
        "UPDATE watchlist SET status='removed', notes=? WHERE symbol=?",
        (note, symbol),
    )
    log.info("morning: %s removed from watchlist (%s)", symbol, note)


def _failed_checks(report: FilterReport, check_names: tuple[str, ...]) -> list[str]:
    return [name for name in check_names if report.checks.get(name) is False]


def _velocity_ratio(conn, symbol: str, asof, source: str) -> float | None:
    """today's mentions / trailing-7-row average, single source only.

    The mentions PK is (symbol, date, source); mixing sources would skew
    the baseline (see data/mentions.py docstring).
    """
    row = conn.execute(
        """SELECT mentions, (SELECT AVG(mentions) FROM (
               SELECT mentions FROM mentions
               WHERE symbol=? AND source=? AND date < ?
               ORDER BY date DESC LIMIT 7)) AS baseline
           FROM mentions WHERE symbol=? AND source=? AND date=?""",
        (symbol, source, asof.isoformat(), symbol, source, asof.isoformat()),
    ).fetchone()
    if not row or not row["baseline"]:
        return None
    try:
        return row["mentions"] / row["baseline"]
    except (TypeError, ZeroDivisionError):
        return None


def _alert_notes(profile, report: FilterReport) -> str:
    notes = []
    if report.checks.get("regime"):
        notes.append("2d wall")
    strength = put_wall_strength(profile)
    if strength is not None:
        notes.append(f"put wall strength {strength:.0%}")
    return " · ".join(notes)


def _alert_payload(card: AlertCard, put_wall_strength_val: float | None) -> dict:
    payload = {
        "spot": card.spot,
        "put_wall": card.put_wall,
        "score": card.score,
        "suggested": card.suggested_entry,
        "notes": card.notes,
    }
    if put_wall_strength_val is not None:
        payload["put_wall_strength"] = round(put_wall_strength_val, 4)
    return payload


def _refresh_earnings(conn, symbol, asof):
    """Update earnings table if row is missing or > 7 days stale."""
    row = conn.execute(
        "SELECT next_earnings_date, updated_at FROM earnings WHERE symbol=?", (symbol,)
    ).fetchone()
    stale = True
    if row and row["updated_at"]:
        try:
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
