"""Known-answer tests for the reference GEX module. THESE PASS NOW -
they are the regression net for any refactor by the implementing model."""
from __future__ import annotations

import math

from tests.conftest import ASOF, EXP, SPOT
from gexwheel.analytics.gex import bs_gamma, compute_profile, contract_gex, put_wall_strength, wall_strength
from gexwheel.models import GexProfile, OptionQuote


def test_bs_gamma_known_value():
    # S=K=100, T=0.25, iv=0.30, r=0 -> d1=0.075, gamma = pdf(0.075)/(100*0.3*0.5)
    g = bs_gamma(100, 100, 0.25, 0.30, r=0.0)
    assert math.isclose(g, 0.026521, rel_tol=1e-3)


def test_gamma_degenerate_inputs_are_zero():
    assert bs_gamma(100, 100, 0.0, 0.3) == 0.0
    assert bs_gamma(100, 100, 0.25, 0.0) == 0.0
    assert bs_gamma(0.0, 100, 0.25, 0.3) == 0.0


def test_contract_gex_sign_convention():
    call = OptionQuote("T", 100, EXP, "C", 100, 0.5)
    put = OptionQuote("T", 100, EXP, "P", 100, 0.5)
    assert contract_gex(call, SPOT, ASOF) > 0
    assert contract_gex(put, SPOT, ASOF) < 0


def test_walls_and_regime(synthetic_chain):
    p = compute_profile("TEST", synthetic_chain, SPOT, ASOF, max_dte=60)
    assert p.call_wall == 110     # engineered max call OI
    assert p.put_wall == 90       # engineered max put OI
    assert p.regime in ("positive", "negative")
    assert p.zero_gamma is None or 75 < p.zero_gamma < 125
    assert set(p.by_strike) == {80, 90, 95, 100, 105, 110, 120}


def test_expired_and_far_options_excluded(synthetic_chain):
    p = compute_profile("TEST", synthetic_chain, SPOT, ASOF, max_dte=10)
    assert p.by_strike == {}  # only expiry is 30 DTE, outside max_dte=10


def test_wall_strength_is_share_of_same_side_absolute_gex():
    p = GexProfile(
        symbol="TEST",
        asof=ASOF,
        spot=SPOT,
        call_wall=110,
        put_wall=90,
        zero_gamma=None,
        net_gex=600,
        regime="positive",
        by_strike={80: -100.0, 90: -300.0, 100: 200.0, 110: 600.0},
    )

    assert put_wall_strength(p) == 0.75
    assert wall_strength(p, "call") == 0.75


def test_wall_strength_is_none_when_wall_or_same_side_gex_missing():
    p = GexProfile(
        symbol="TEST",
        asof=ASOF,
        spot=SPOT,
        call_wall=None,
        put_wall=90,
        zero_gamma=None,
        net_gex=0,
        regime="positive",
        by_strike={90: 0.0},
    )

    assert put_wall_strength(p) is None
    assert wall_strength(p, "call") is None
