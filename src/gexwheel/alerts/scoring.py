"""Setup scoring + alert decision.

should_alert(profile, cfg, conn, asof, *, implied_move_pct) -> bool
  * profile.put_wall is not None
  * spot >= put_wall and (spot - put_wall) / spot <= within_implied_move_fraction
    × implied_move_pct (ATM straddle / IV move from the morning chain)
  * wall persistence: db.recent_put_walls(symbol, wall_persistence_days)
  * no duplicate alert row for (symbol, asof, 'put_wall_entry')

score(profile, iv_rank_val, vrp_val, velocity_ratio, *, implied_move_pct) -> float
  Proximity component uses the same implied-move band as should_alert.

suggested_entry(profile, implied_move_pct=None) -> str
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date

from .. import db as gdb
from ..models import GexProfile

WEIGHTS = {
    "iv_rank":   35,
    "vrp":       25,
    "regime":    20,
    "velocity":  10,
    "proximity": 10,
}


def wall_distance_pct(spot: float, put_wall: float | None) -> float | None:
    """Fraction of spot above put_wall, or None if spot is below the wall."""
    if put_wall is None or spot <= 0 or spot < put_wall:
        return None
    return (spot - put_wall) / spot


def within_im_proximity(
    spot: float,
    put_wall: float | None,
    implied_move_pct: float | None,
    max_im_fraction: float,
) -> bool:
    """True when spot is above the wall and within max_im_fraction × implied move."""
    dist = wall_distance_pct(spot, put_wall)
    if dist is None or implied_move_pct is None or implied_move_pct <= 0:
        return False
    return dist <= max_im_fraction * implied_move_pct


def proximity_score_fraction(
    spot: float,
    put_wall: float | None,
    implied_move_pct: float | None,
    max_im_fraction: float,
) -> float:
    """0..1 proximity credit: 1.0 at the wall, 0.0 at or beyond the IM band."""
    dist = wall_distance_pct(spot, put_wall)
    if dist is None or implied_move_pct is None or implied_move_pct <= 0:
        return 0.0
    threshold = max_im_fraction * implied_move_pct
    if threshold <= 0:
        return 0.0
    return max(0.0, 1.0 - dist / threshold)


def should_alert(
    profile: GexProfile,
    cfg: dict,
    conn: sqlite3.Connection,
    asof: date,
    *,
    implied_move_pct: float | None,
) -> bool:
    """True when wall proximity (IM band), persistence, and dedup all pass."""
    a = cfg["alerts"]
    max_im_fraction = a.get("within_implied_move_fraction", 0.30)
    persistence_days = a.get("wall_persistence_days", 2)

    if profile.put_wall is None:
        return False

    if not within_im_proximity(
        profile.spot, profile.put_wall, implied_move_pct, max_im_fraction,
    ):
        return False

    recent_walls = gdb.recent_put_walls(conn, profile.symbol, persistence_days)
    if len(recent_walls) < persistence_days:
        return False
    if not all(
        w is not None and math.isclose(w, profile.put_wall, rel_tol=1e-4)
        for w in recent_walls
    ):
        return False

    dup = conn.execute(
        "SELECT 1 FROM alerts WHERE symbol=? AND date=? AND type=?",
        (profile.symbol, asof.isoformat(), "put_wall_entry"),
    ).fetchone()
    return dup is None


def score(
    profile: GexProfile,
    iv_rank_val: float | None,
    vrp_val: float | None,
    velocity_ratio: float | None,
    *,
    implied_move_pct: float | None,
    cfg: dict | None = None,
) -> float:
    """0-100 composite score for ranking alerts. Higher = better entry."""
    max_im_fraction = 0.30
    if cfg is not None:
        max_im_fraction = cfg.get("alerts", {}).get("within_implied_move_fraction", 0.30)

    s = 0.0

    if iv_rank_val is not None:
        s += WEIGHTS["iv_rank"] * min(iv_rank_val / 100.0, 1.0)

    if vrp_val is not None:
        s += WEIGHTS["vrp"] * min(max(vrp_val, 0.0) / 0.15, 1.0)

    if profile.regime == "positive":
        s += WEIGHTS["regime"]

    if velocity_ratio is not None:
        s += WEIGHTS["velocity"] * min(velocity_ratio / 5.0, 1.0)

    prox_frac = proximity_score_fraction(
        profile.spot, profile.put_wall, implied_move_pct, max_im_fraction,
    )
    s += WEIGHTS["proximity"] * prox_frac

    return round(max(0.0, min(s, 100.0)), 1)


def suggested_entry(profile: GexProfile, implied_move_pct: float | None = None) -> str:
    """Human-readable entry suggestion for the dashboard."""
    if profile.put_wall is None:
        return "No put wall identified"
    dist = wall_distance_pct(profile.spot, profile.put_wall)
    if dist is None:
        return f"CSP {profile.put_wall:.2f}P — spot below put wall"
    pct_above = dist * 100
    if implied_move_pct and implied_move_pct > 0:
        im_frac = dist / implied_move_pct
        return (
            f"CSP {profile.put_wall:.2f}P, 30-45 DTE (~0.20-0.30 delta), "
            f"spot {profile.spot:.2f} sits {pct_above:.1f}% above wall "
            f"({im_frac:.0%} of implied move)"
        )
    return (
        f"CSP {profile.put_wall:.2f}P, 30-45 DTE (~0.20-0.30 delta), "
        f"spot {profile.spot:.2f} sits {pct_above:.1f}% above the wall"
    )
