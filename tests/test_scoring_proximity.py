"""Scoring proximity uses implied-move band."""
from __future__ import annotations

from datetime import date

from gexwheel.alerts.scoring import proximity_score_fraction, score
from gexwheel.models import GexProfile

CFG = {"alerts": {"within_implied_move_fraction": 0.30}}


def _profile(spot: float, put_wall: float = 18.0) -> GexProfile:
    return GexProfile(
        symbol="T", asof=date(2026, 6, 10), spot=spot,
        call_wall=22.0, put_wall=put_wall, zero_gamma=20.0,
        net_gex=1.0, regime="positive",
    )


def test_proximity_score_full_at_wall():
    assert proximity_score_fraction(18.0, 18.0, 0.10, 0.30) == 1.0


def test_proximity_score_zero_beyond_band():
    assert proximity_score_fraction(20.0, 18.0, 0.10, 0.30) == 0.0


def test_score_includes_proximity_component():
    at_wall = score(_profile(18.0), 70.0, 0.1, None, implied_move_pct=0.10, cfg=CFG)
    far = score(_profile(20.0), 70.0, 0.1, None, implied_move_pct=0.10, cfg=CFG)
    assert at_wall > far
