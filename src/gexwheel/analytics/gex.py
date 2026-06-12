"""GEX (dealer gamma exposure) math. FULLY IMPLEMENTED - reference module.

Methodology (standard naive dealer-positioning convention, SpotGamma-style):
  * Assume dealers are net LONG calls and net SHORT puts (customers mostly
    buy puts for protection and sell calls for income). Therefore:
      call GEX  -> positive contribution (dealer hedging dampens moves)
      put GEX   -> negative contribution (dealer hedging amplifies moves)
  * Per-contract dollar gamma per 1% spot move:
        GEX = gamma * OI * 100 (contract size) * spot^2 * 0.01
  * Black-Scholes gamma:
        d1 = (ln(S/K) + (r + sigma^2/2) * T) / (sigma * sqrt(T))
        gamma = phi(d1) / (S * sigma * sqrt(T)),  phi = standard normal pdf
  * Call wall  = strike with the largest positive aggregate GEX
  * Put wall   = strike with the most negative aggregate GEX
  * Zero gamma = hypothetical spot level where total net GEX crosses zero,
    found by re-evaluating the whole book on a spot grid (+-25%) and
    linearly interpolating the sign change nearest to current spot.

These are *estimates*: real dealer positioning is unknowable from OI alone.
Walls matter because they persist and because everyone else computes them
the same way - they are Schelling points, not physics.
"""
from __future__ import annotations

import math
from datetime import date

from ..models import GexProfile, OptionQuote

_SQRT_2PI = math.sqrt(2.0 * math.pi)
CONTRACT_SIZE = 100


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def bs_gamma(spot: float, strike: float, t_years: float, iv: float, r: float = 0.045) -> float:
    """Black-Scholes gamma (identical for calls and puts). Returns 0 on degenerate inputs."""
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv <= 0:
        return 0.0
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    return _norm_pdf(d1) / (spot * iv * sqrt_t)


def contract_gex(q: OptionQuote, spot: float, asof: date, r: float = 0.045) -> float:
    """Signed dollar gamma per 1%% move for one contract line (all OI)."""
    t = (q.expiry - asof).days / 365.0
    if t <= 0 or q.open_interest <= 0:
        return 0.0
    g = bs_gamma(spot, q.strike, t, q.iv, r)
    gex = g * q.open_interest * CONTRACT_SIZE * spot * spot * 0.01
    return gex if q.kind == "C" else -gex


def _net_gex_at_spot(quotes: list[OptionQuote], spot: float, asof: date, r: float) -> float:
    return sum(contract_gex(q, spot, asof, r) for q in quotes)


def wall_strength(profile: GexProfile, kind: str) -> float | None:
    """Wall GEX as share of same-side absolute strike GEX, 0-1."""
    if kind == "call":
        wall = profile.call_wall
        side_values = [v for v in profile.by_strike.values() if v > 0]
    elif kind == "put":
        wall = profile.put_wall
        side_values = [-v for v in profile.by_strike.values() if v < 0]
    else:
        raise ValueError("kind must be 'call' or 'put'")

    if wall is None:
        return None
    wall_value = profile.by_strike.get(wall)
    if wall_value is None:
        return None
    total = sum(side_values)
    if total <= 0:
        return None
    return abs(wall_value) / total


def put_wall_strength(profile: GexProfile) -> float | None:
    """Put wall dominance as a 0-1 share of same-side absolute GEX."""
    return wall_strength(profile, "put")


def compute_profile(symbol: str, quotes: list[OptionQuote], spot: float, asof: date,
                    *, r: float = 0.045, max_dte: int = 60) -> GexProfile:
    """Aggregate a chain into a GexProfile. Filters to expiries within max_dte."""
    book = [q for q in quotes if 0 < (q.expiry - asof).days <= max_dte]

    by_strike: dict[float, float] = {}
    for q in book:
        by_strike[q.strike] = by_strike.get(q.strike, 0.0) + contract_gex(q, spot, asof, r)

    call_wall = put_wall = None
    if by_strike:
        positives = {k: v for k, v in by_strike.items() if v > 0}
        negatives = {k: v for k, v in by_strike.items() if v < 0}
        if positives:
            call_wall = max(positives, key=positives.get)
        if negatives:
            put_wall = min(negatives, key=negatives.get)

    net = sum(by_strike.values())

    # Zero-gamma flip: scan a spot grid +-25%, find sign change closest to spot.
    zero_gamma = None
    if book:
        lo, hi, steps = spot * 0.75, spot * 1.25, 50
        step = (hi - lo) / steps
        prev_s, prev_v = lo, _net_gex_at_spot(book, lo, asof, r)
        best_dist = float("inf")
        for i in range(1, steps + 1):
            s = lo + i * step
            v = _net_gex_at_spot(book, s, asof, r)
            if prev_v == 0.0 or (prev_v < 0) != (v < 0):
                # linear interpolation between (prev_s, prev_v) and (s, v)
                if v != prev_v:
                    cross = prev_s + (0.0 - prev_v) * (s - prev_s) / (v - prev_v)
                else:
                    cross = prev_s
                if abs(cross - spot) < best_dist:
                    best_dist, zero_gamma = abs(cross - spot), round(cross, 2)
            prev_s, prev_v = s, v

    return GexProfile(
        symbol=symbol, asof=asof, spot=spot,
        call_wall=call_wall, put_wall=put_wall, zero_gamma=zero_gamma,
        net_gex=net, regime="positive" if net >= 0 else "negative",
        by_strike=by_strike,
    )
