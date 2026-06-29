"""Weekday intraday identify job (~10:45 ET).

Spot₀ is captured at ~10:15 by jobs/morning_snapshot.py (equity only).
This job pulls option chains at spot₁, session low, GEX, filters, and identifies
trades subject to freshness gates (move since spot₀, session low vs put wall).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import db as gdb
from ..alerts.freshness import identification_block_reason
from ..alerts.scoring import score, should_alert
from ..alerts.scoring import suggested_entry as make_entry
from ..analytics import gex as gex_mod
from ..analytics import vol as vol_mod
from ..analytics.gex import put_wall_strength
from ..data.chains import make_chain_source
from ..data.prices import PriceFetchError, daily_closes, next_earnings, sector, session_low
from ..models import AlertCard, FilterReport
from ..screening.filters import run_filters

log = logging.getLogger(__name__)

# Active names are demoted only on the still-daily checks. Structural gating
# (price/volume/oi/spread/vrp/sector/blocklist) lives in the periodic screen.
_DAILY_REMOVE_CHECKS = ("above_50dma", "earnings")


def run(cfg: dict) -> None:
    """Weekday pre-market: refresh GEX, run filters, identify and persist trades."""
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    asof = datetime.now(tz).date()
    log.info("morning: starting for %s", asof)

    conn = gdb.connect(cfg["db_path"])
    chain_src = make_chain_source(cfg)
    data_cfg = cfg["data"]
    alert_cfg = cfg["alerts"]
    cooldown_days = alert_cfg.get("cooldown_days", 5)
    spot0_map = gdb.get_morning_spot0(conn, asof)
    if not spot0_map:
        log.warning("morning: no spot0 snapshot for %s — move-since-spot0 gate skipped", asof)
    else:
        log.info("morning: loaded spot0 for %d symbols", len(spot0_map))

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
                cooldown_days, spot0_map, cards, alert_payloads
            )
        except Exception as exc:
            log.error("morning: unhandled error for %s: %s", symbol, exc, exc_info=True)

    # Persist every identified trade (the dashboard is the delivery surface now).
    if cards:
        now_iso = datetime.now(tz).isoformat()
        _persist_trades(conn, cards, alert_payloads, asof, now_iso)
        log.info("morning: identified %d trades", len(cards))
    else:
        log.info("morning: no trades identified")

    conn.commit()
    conn.close()


def _process_symbol(symbol, asof, conn, chain_src, cfg, data_cfg,
                    cooldown_days, spot0_map, cards, alert_payloads):
    """Per-symbol processing block. All exceptions bubble to the caller's handler."""

    # 1. Fetch chain (spot₁ + options at identify time)
    spot1, quotes = chain_src.fetch(symbol, asof, data_cfg["max_dte"])
    spot0 = spot0_map.get(symbol)

    session_low_val: float | None = None
    try:
        session_low_val = session_low(symbol)
    except PriceFetchError as exc:
        log.warning("morning: %s session low unavailable: %s", symbol, exc)

    # 2. Fetch closes
    closes = daily_closes(symbol)

    # 3. GEX profile
    profile = gex_mod.compute_profile(
        symbol, quotes, spot1, asof,
        r=data_cfg.get("risk_free_rate", 0.045),
        max_dte=data_cfg["max_dte"],
    )
    gdb.record_gex(conn, profile)

    # 4. Vol stats
    try:
        rv = vol_mod.realized_vol(closes)
    except ValueError:
        rv = None

    iv = vol_mod.atm_iv(quotes, spot1, asof)

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
        spot=spot1, quotes=quotes, closes=closes,
        gex_profile=profile, asof=asof,
        iv_rank_val=ivr, vrp_val=vrp,
    )
    log.info("morning: %s filters passed=%s checks=%s", symbol, report.passed, report.checks)

    # Promote/demote watchlist membership
    _update_watchlist_membership(symbol, report, conn, asof)

    # 7. Wall-break: session low or spot₁ below put wall
    if profile.put_wall is not None:
        if session_low_val is not None and session_low_val < profile.put_wall:
            bench_until = asof + timedelta(days=cooldown_days)
            gdb.bench_ticker(conn, symbol, bench_until, "session low below put wall")
            log.warning(
                "morning: %s benched until %s (session low %.2f < put wall %.2f)",
                symbol, bench_until, session_low_val, profile.put_wall,
            )
            return
        if spot1 < profile.put_wall:
            bench_until = asof + timedelta(days=cooldown_days)
            gdb.bench_ticker(conn, symbol, bench_until, "closed below put wall")
            log.warning("morning: %s benched until %s (below put wall %.2f)", symbol, bench_until, profile.put_wall)
            return

    implied_move = vol_mod.implied_move_pct(quotes, spot1, asof)
    block = identification_block_reason(
        spot0=spot0,
        spot1=spot1,
        session_low=session_low_val,
        put_wall=profile.put_wall,
        implied_move_pct=implied_move,
        cfg=cfg,
    )
    if block:
        log.info("morning: %s identification blocked (%s)", symbol, block)
        return

    # 8. Identify trade
    if report.passed and should_alert(profile, cfg, conn, asof, implied_move_pct=implied_move):
        # NOTE: velocity context follows the configured discovery source;
        # 'both' falls back to apewisdom (the primary).
        mention_source = "praw" if cfg["reddit"].get("source") == "praw" else "apewisdom"
        vel_ratio = _velocity_ratio(conn, symbol, asof, mention_source)

        card_score = score(profile, ivr, vrp, vel_ratio, implied_move_pct=implied_move, cfg=cfg)
        strength = put_wall_strength(profile)
        card = AlertCard(
            symbol=symbol,
            alert_type="put_wall_entry",
            spot=spot1,
            put_wall=profile.put_wall,
            call_wall=profile.call_wall,
            zero_gamma=profile.zero_gamma,
            regime=profile.regime,
            iv_rank=ivr,
            vrp=vrp,
            score=card_score,
            suggested_entry=make_entry(profile, implied_move),
            notes=_alert_notes(profile, report),
        )
        cards.append(card)
        alert_payloads[(card.symbol, card.alert_type)] = _alert_payload(
            card,
            put_wall_strength_val=strength,
            spot0=spot0,
            spot1=spot1,
            session_low=session_low_val,
            implied_move_pct=implied_move,
        )
        log.info("morning: %s generated alert score=%.1f", symbol, card_score)


def _update_watchlist_membership(symbol: str, report: FilterReport, conn, asof) -> None:
    """Demote an active name only when a still-daily check fails.

    Promotion onto the watchlist happens in screening.discovery (velocity);
    structural entry-gating happens in jobs.screen. The morning job's job here
    is just to drop names that fail a daily, time-sensitive check.
    """
    failures = _failed_checks(report, _DAILY_REMOVE_CHECKS)
    if not failures:
        return
    note = f"daily fail: {', '.join(failures)}"
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


def _persist_trades(conn, cards, alert_payloads, asof, now_iso: str) -> None:
    """Write every identified trade to the alerts table, highest score first.

    There is no separate delivery step anymore: the dashboard reads these rows,
    so sent_at records the identification/publish time for every trade.
    """
    for card in sorted(cards, key=lambda c: c.score, reverse=True):
        gdb.log_alert(
            conn, card.symbol, asof, card.alert_type,
            alert_payloads[(card.symbol, card.alert_type)],
            now_iso,
        )


def _alert_notes(profile, report: FilterReport) -> str:
    notes = []
    if report.checks.get("regime"):
        notes.append("2d wall")
    strength = put_wall_strength(profile)
    if strength is not None:
        notes.append(f"put wall strength {strength:.0%}")
    return " · ".join(notes)


def _alert_payload(
    card: AlertCard,
    *,
    put_wall_strength_val: float | None,
    spot0: float | None = None,
    spot1: float | None = None,
    session_low: float | None = None,
    implied_move_pct: float | None = None,
) -> dict:
    payload = {
        "spot": card.spot,
        "put_wall": card.put_wall,
        "score": card.score,
        "suggested": card.suggested_entry,
        "notes": card.notes,
    }
    if put_wall_strength_val is not None:
        payload["put_wall_strength"] = round(put_wall_strength_val, 4)
    if spot0 is not None:
        payload["spot0"] = round(spot0, 4)
    if spot1 is not None:
        payload["spot1"] = round(spot1, 4)
    if session_low is not None:
        payload["session_low"] = round(session_low, 4)
    if implied_move_pct is not None:
        payload["implied_move_pct"] = round(implied_move_pct, 4)
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
