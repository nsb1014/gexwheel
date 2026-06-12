"""Setup scoring + alert decision.

should_alert(profile, cfg, conn, asof) -> bool
  * profile.put_wall is not None
  * spot within put_wall_proximity_pct of put wall AND spot >= put_wall
    (we want price approaching the wall from ABOVE - sitting on support;
    if spot < put_wall the wall failed: caller benches the ticker instead)
  * wall persistence: db.recent_put_walls(symbol, wall_persistence_days)
    all equal to the current put_wall strike (use math.isclose, walls are
    floats). Fewer rows than required days -> not persistent -> False.
  * no duplicate: no row in alerts table for (symbol, asof, 'put_wall_entry').

score(profile, iv_rank_val, vrp_val, velocity_ratio) -> float
  Simple 0-100 composite for ranking which alerts to send first
  (max_alerts_per_run caps the Discord batch):
    +35 * min(iv_rank_val / 100, 1)                    [premium richness]
    +25 * min(max(vrp_val, 0) / 0.15, 1)               [edge: IV over RV, 15 vol pts = full credit]
    +20 if profile.regime == 'positive' else 0          [dampened tape]
    +10 * min(velocity_ratio / 5, 1) if velocity_ratio else 0   [attention tailwind]
    +10 * (1 - proximity/put_wall_proximity_pct)        [closer to wall = better entry]
  Clamp 0-100, round(1). Weights in one WEIGHTS dict at module top so they
  are tunable without touching logic.

suggested_entry string (used by the job when building AlertCard):
  f"CSP {put_wall}P, 30-45 DTE (~0.20-0.30 delta), spot {spot:.2f} sits
    {pct:.1f}% above the wall"
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date

from ..models import GexProfile

# Scoring weights — tune here without touching logic
WEIGHTS = {
    "iv_rank":   35,
    "vrp":       25,
    "regime":    20,
    "velocity":  10,
    "proximity": 10,
}


def should_alert(profile: GexProfile, cfg: dict, conn: sqlite3.Connection, asof: date) -> bool:
    """True only when: put wall exists, spot is above it and within proximity
    threshold, wall has persisted N consecutive days, and no duplicate alert today."""
    a = cfg["alerts"]
    proximity_pct = a.get("put_wall_proximity_pct", 0.03)
    persistence_days = a.get("wall_persistence_days", 2)

    if profile.put_wall is None:
        return False

    # Spot must be above the put wall and within proximity_pct of it
    if profile.spot < profile.put_wall:
        return False
    distance = (profile.spot - profile.put_wall) / profile.spot
    if distance > proximity_pct:
        return False

    # Wall persistence: the same strike must appear in the last N GEX snapshots
    from .. import db as gdb
    recent_walls = gdb.recent_put_walls(conn, profile.symbol, persistence_days)
    if len(recent_walls) < persistence_days:
        return False
    if not all(
        w is not None and math.isclose(w, profile.put_wall, rel_tol=1e-4)
        for w in recent_walls
    ):
        return False

    # No duplicate: no alert of this type already logged for today
    dup = conn.execute(
        "SELECT 1 FROM alerts WHERE symbol=? AND date=? AND type=?",
        (profile.symbol, asof.isoformat(), "put_wall_entry"),
    ).fetchone()
    return dup is None


def score(profile: GexProfile, iv_rank_val: float | None, vrp_val: float | None,
          velocity_ratio: float | None) -> float:
    """0-100 composite score for ranking alerts. Higher = better entry."""
    proximity_pct = 0.03  # default; scoring doesn't have cfg access, use module-level default

    s = 0.0

    # IV richness
    if iv_rank_val is not None:
        s += WEIGHTS["iv_rank"] * min(iv_rank_val / 100.0, 1.0)

    # VRP edge: 15 vol-point spread = full credit
    if vrp_val is not None:
        s += WEIGHTS["vrp"] * min(max(vrp_val, 0.0) / 0.15, 1.0)

    # Regime
    if profile.regime == "positive":
        s += WEIGHTS["regime"]

    # Mention velocity tailwind
    if velocity_ratio is not None:
        s += WEIGHTS["velocity"] * min(velocity_ratio / 5.0, 1.0)

    # Proximity: closer to wall = better entry (0 distance = full credit)
    if profile.put_wall is not None and profile.put_wall > 0:
        prox = (profile.spot - profile.put_wall) / profile.spot
        prox_score = 1.0 - (prox / proximity_pct) if proximity_pct > 0 else 0.0
        s += WEIGHTS["proximity"] * max(prox_score, 0.0)

    return round(max(0.0, min(s, 100.0)), 1)


def suggested_entry(profile: GexProfile) -> str:
    """Human-readable entry suggestion for the Discord card."""
    if profile.put_wall is None:
        return "No put wall identified"
    pct = (profile.spot - profile.put_wall) / profile.spot * 100
    return (
        f"CSP {profile.put_wall:.2f}P, 30-45 DTE (~0.20-0.30 delta), "
        f"spot {profile.spot:.2f} sits {pct:.1f}% above the wall"
    )
