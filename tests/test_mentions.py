"""ApeWisdom fetch behavior (no live network)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from gexwheel.data.mentions import MentionFetchError, fetch_apewisdom


def test_fetch_apewisdom_raises_when_all_pages_fail():
    with patch("gexwheel.data.mentions._get_with_retry", side_effect=MentionFetchError("down")):
        with pytest.raises(MentionFetchError, match="all 2 apewisdom page"):
            fetch_apewisdom("wallstreetbets", pages=2, asof=date(2026, 6, 10))


def test_fetch_apewisdom_returns_empty_when_api_ok_but_no_tickers():
    with patch("gexwheel.data.mentions._get_with_retry", return_value={"results": []}):
        assert fetch_apewisdom("wallstreetbets", pages=1, asof=date(2026, 6, 10)) == []
