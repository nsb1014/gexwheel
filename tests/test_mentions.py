"""ApeWisdom fetch behavior (no live network)."""
from __future__ import annotations

from datetime import date
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from gexwheel import db as gdb
from gexwheel.data.mentions import MentionFetchError, fetch_apewisdom, fetch_praw


def test_fetch_apewisdom_raises_when_all_pages_fail():
    with patch("gexwheel.data.mentions._get_with_retry", side_effect=MentionFetchError("down")):
        with pytest.raises(MentionFetchError, match="all 2 apewisdom page"):
            fetch_apewisdom("wallstreetbets", pages=2, asof=date(2026, 6, 10))


def test_fetch_apewisdom_returns_empty_when_api_ok_but_no_tickers():
    with patch("gexwheel.data.mentions._get_with_retry", return_value={"results": []}):
        assert fetch_apewisdom("wallstreetbets", pages=1, asof=date(2026, 6, 10)) == []


def test_fetch_praw_counts_cashtags_and_bare_known_symbols(monkeypatch, tmp_path):
    asof = date(2026, 6, 10)
    db_path = tmp_path / "mentions.db"
    conn = gdb.connect(str(db_path))
    gdb.upsert_ticker(conn, "TEST", source="manual", asof=asof)
    gdb.upsert_ticker(conn, "AAPL", source="manual", asof=asof)
    conn.commit()
    conn.close()
    post_created_utc = datetime(2026, 6, 10, 12, tzinfo=ZoneInfo("America/New_York")).timestamp()

    class FakeComment:
        body = "$AAPL and TEST"
        created_utc = post_created_utc

    class FakeComments:
        def replace_more(self, limit=0):
            return None

        def list(self):
            return [FakeComment()]

    class FakePost:
        title = "$AAPL breakout"
        selftext = "TEST looks interesting"
        created_utc = post_created_utc
        comments = FakeComments()

    class FakeSubreddit:
        def new(self, limit=500):
            return [FakePost()]

    class FakeReddit:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def subreddit(self, name):
            assert name == "wallstreetbets"
            return FakeSubreddit()

    monkeypatch.setitem(
        __import__("sys").modules,
        "praw",
        SimpleNamespace(Reddit=FakeReddit),
    )
    cfg = {
        "db_path": str(db_path),
        "timezone": "America/New_York",
        "reddit": {
            "praw": {
                "client_id": "id",
                "client_secret": "secret",
                "user_agent": "gexwheel/test",
            }
        },
    }

    records = sorted(fetch_praw(cfg, asof), key=lambda r: r.symbol)

    assert [(r.symbol, r.mentions, r.source) for r in records] == [
        ("AAPL", 2, "praw"),
        ("TEST", 2, "praw"),
    ]


def test_fetch_praw_retries_transient_listing_failure(monkeypatch, tmp_path):
    asof = date(2026, 6, 10)
    db_path = tmp_path / "mentions.db"
    conn = gdb.connect(str(db_path))
    gdb.upsert_ticker(conn, "TEST", source="manual", asof=asof)
    conn.commit()
    conn.close()
    post_created_utc = datetime(2026, 6, 10, 12, tzinfo=ZoneInfo("America/New_York")).timestamp()
    attempts = {"new": 0}

    class FakeComments:
        def replace_more(self, limit=0):
            return None

        def list(self):
            return []

    class FakePost:
        title = "TEST"
        selftext = ""
        created_utc = post_created_utc
        comments = FakeComments()

    class FakeSubreddit:
        def new(self, limit=500):
            attempts["new"] += 1
            if attempts["new"] == 1:
                raise RuntimeError("transient")
            return [FakePost()]

    class FakeReddit:
        def __init__(self, **kwargs):
            assert kwargs["requestor_kwargs"]["timeout"] == 15

        def subreddit(self, name):
            return FakeSubreddit()

    monkeypatch.setitem(
        __import__("sys").modules,
        "praw",
        SimpleNamespace(Reddit=FakeReddit),
    )
    monkeypatch.setattr("gexwheel.data.mentions.time.sleep", lambda seconds: None)
    cfg = {
        "db_path": str(db_path),
        "timezone": "America/New_York",
        "reddit": {
            "praw": {
                "client_id": "id",
                "client_secret": "secret",
                "user_agent": "gexwheel/test",
            }
        },
    }

    records = fetch_praw(cfg, asof)

    assert attempts["new"] == 2
    assert [(r.symbol, r.mentions) for r in records] == [("TEST", 1)]
