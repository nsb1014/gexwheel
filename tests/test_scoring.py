"""Alert scoring and should-alert edge cases."""
from __future__ import annotations

from datetime import date, timedelta

from gexwheel import db as gdb
from gexwheel.alerts.scoring import should_alert
from gexwheel.models import GexProfile


ASOF = date(2026, 6, 10)


def _profile(spot: float, put_wall: float | None = 18.0, asof: date = ASOF) -> GexProfile:
    return GexProfile(
        symbol="TEST",
        asof=asof,
        spot=spot,
        call_wall=22.0,
        put_wall=put_wall,
        zero_gamma=20.0,
        net_gex=1.0,
        regime="positive",
    )


def _conn_with_recent_walls(*walls: float):
    conn = gdb.connect(":memory:")
    for idx, wall in enumerate(walls):
        gdb.record_gex(conn, _profile(spot=20.0, put_wall=wall, asof=ASOF - timedelta(days=idx)))
    conn.commit()
    return conn


def _cfg():
    return {"alerts": {"put_wall_proximity_pct": 0.03, "wall_persistence_days": 2}}


def test_should_alert_allows_spot_exactly_at_persistent_put_wall():
    conn = _conn_with_recent_walls(18.0, 18.0)

    assert should_alert(_profile(spot=18.0), _cfg(), conn, ASOF) is True


def test_should_alert_rejects_spot_below_put_wall():
    conn = _conn_with_recent_walls(18.0, 18.0)

    assert should_alert(_profile(spot=17.99), _cfg(), conn, ASOF) is False


def test_should_alert_requires_configured_wall_persistence():
    conn = _conn_with_recent_walls(18.0)

    assert should_alert(_profile(spot=18.0), _cfg(), conn, ASOF) is False
