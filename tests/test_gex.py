"""Known-answer tests for the reference GEX module. THESE PASS NOW -
they are the regression net for any refactor by the implementing model."""
from __future__ import annotations

import math

from tests.conftest import ASOF, EXP, SPOT
from gexwheel.analytics.gex import bs_gamma, compute_profile, contract_gex
from gexwheel.models import OptionQuote


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
