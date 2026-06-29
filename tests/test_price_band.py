"""Price band helper."""
from gexwheel.screening.filters import check_price_range


def test_no_max_allows_high_spot():
    assert check_price_range(150.0, 5.0, None) is True


def test_below_min_fails():
    assert check_price_range(4.99, 5.0, None) is False


def test_max_when_set():
    assert check_price_range(45.0, 5.0, 45.0) is True
    assert check_price_range(45.01, 5.0, 45.0) is False
