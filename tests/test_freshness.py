"""Intraday identification freshness gates."""
from __future__ import annotations

from gexwheel.alerts.freshness import identification_block_reason


def _cfg(**overrides):
    base = {
        "alerts": {
            "max_move_since_spot0_im_fraction": 0.50,
            "require_spot0_snapshot": False,
        }
    }
    base["alerts"].update(overrides)
    return base


def test_blocks_when_session_low_crossed_put_wall():
    reason = identification_block_reason(
        spot0=20.0, spot1=19.5, session_low=17.5, put_wall=18.0,
        implied_move_pct=0.10, cfg=_cfg(),
    )
    assert reason == "session_low_below_put_wall"


def test_blocks_large_move_since_spot0_vs_implied_move():
    # 5% move, IM 8% -> 5% > 0.5*8%=4%
    reason = identification_block_reason(
        spot0=20.0, spot1=21.0, session_low=19.8, put_wall=18.0,
        implied_move_pct=0.08, cfg=_cfg(),
    )
    assert reason == "move_since_spot0"


def test_allows_small_move_since_spot0():
    assert identification_block_reason(
        spot0=20.0, spot1=20.2, session_low=19.8, put_wall=18.0,
        implied_move_pct=0.10, cfg=_cfg(),
    ) is None


def test_requires_spot0_when_configured():
    reason = identification_block_reason(
        spot0=None, spot1=20.0, session_low=19.8, put_wall=18.0,
        implied_move_pct=0.10, cfg=_cfg(require_spot0_snapshot=True),
    )
    assert reason == "missing_spot0_snapshot"
