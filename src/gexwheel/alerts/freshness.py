"""Intraday freshness gates applied at trade identification (10:45 ET run)."""
from __future__ import annotations


def identification_block_reason(
    *,
    spot0: float | None,
    spot1: float,
    session_low: float | None,
    put_wall: float | None,
    implied_move_pct: float | None,
    cfg: dict,
) -> str | None:
    """Return a short reason string when the trade must not be identified, else None."""
    a = cfg.get("alerts", {})

    if put_wall is not None and session_low is not None:
        if session_low < put_wall:
            return "session_low_below_put_wall"

    if spot0 is not None and spot0 > 0:
        move_pct = abs(spot1 - spot0) / spot0
        max_im_frac = a.get("max_move_since_spot0_im_fraction")
        if max_im_frac is not None and implied_move_pct is not None and implied_move_pct > 0:
            if move_pct > max_im_frac * implied_move_pct:
                return "move_since_spot0"
    elif a.get("require_spot0_snapshot", False):
        return "missing_spot0_snapshot"

    return None
