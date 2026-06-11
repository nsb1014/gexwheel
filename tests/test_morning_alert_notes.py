"""Morning alert note formatting."""
from __future__ import annotations

from datetime import date

from gexwheel.jobs.morning import _alert_notes, _alert_payload
from gexwheel.models import AlertCard, FilterReport, GexProfile


def test_alert_notes_include_put_wall_strength_percentage():
    profile = GexProfile(
        symbol="TEST",
        asof=date(2026, 6, 10),
        spot=20.0,
        call_wall=22.0,
        put_wall=18.0,
        zero_gamma=19.0,
        net_gex=100.0,
        regime="positive",
        by_strike={17.0: -25.0, 18.0: -75.0, 22.0: 100.0},
    )
    report = FilterReport("TEST", True, checks={"regime": True}, values={})

    assert _alert_notes(profile, report) == "2d wall · put wall strength 75%"


def test_alert_notes_omit_wall_strength_when_unavailable():
    profile = GexProfile(
        symbol="TEST",
        asof=date(2026, 6, 10),
        spot=20.0,
        call_wall=None,
        put_wall=None,
        zero_gamma=None,
        net_gex=0.0,
        regime="positive",
        by_strike={},
    )
    report = FilterReport("TEST", True, checks={"regime": True}, values={})

    assert _alert_notes(profile, report) == "2d wall"


def test_alert_payload_includes_numeric_put_wall_strength():
    card = AlertCard(
        symbol="TEST",
        alert_type="put_wall_entry",
        spot=20.0,
        put_wall=18.0,
        call_wall=22.0,
        zero_gamma=19.0,
        regime="positive",
        iv_rank=75.0,
        vrp=0.10,
        score=88.0,
        suggested_entry="CSP 18P",
        notes="2d wall · put wall strength 75%",
    )

    assert _alert_payload(card, put_wall_strength_val=0.75) == {
        "spot": 20.0,
        "put_wall": 18.0,
        "score": 88.0,
        "suggested": "CSP 18P",
        "notes": "2d wall · put wall strength 75%",
        "put_wall_strength": 0.75,
    }
