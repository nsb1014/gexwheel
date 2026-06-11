"""Discord alert delivery (mocked HTTP)."""
from __future__ import annotations

from unittest.mock import patch

from gexwheel.alerts.discord import send_alerts
from gexwheel.models import AlertCard


def _card(symbol: str, score: float) -> AlertCard:
    return AlertCard(
        symbol=symbol,
        alert_type="put_wall_entry",
        spot=20.0,
        put_wall=18.0,
        call_wall=22.0,
        zero_gamma=20.0,
        regime="positive",
        iv_rank=70.0,
        vrp=0.1,
        score=score,
        suggested_entry="test",
        notes="",
    )


def test_send_alerts_marks_only_successful_chunks():
    cards = [_card("AAA", 90), _card("BBB", 80), _card("CCC", 70)]
    cfg = {"discord": {"webhook_url": "http://example", "max_alerts_per_run": 3}}

    with patch("gexwheel.alerts.discord._CHUNK", 2), patch(
        "gexwheel.alerts.discord._post", side_effect=[True, False]
    ):
        sent = send_alerts(cards, cfg)

    assert [c.symbol for c in sent] == ["AAA", "BBB"]


def test_send_alerts_returns_empty_when_all_chunks_fail():
    cards = [_card("AAA", 90)]
    cfg = {"discord": {"webhook_url": "http://example", "max_alerts_per_run": 8}}

    with patch("gexwheel.alerts.discord._post", return_value=False):
        assert send_alerts(cards, cfg) == []
