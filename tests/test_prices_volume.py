"""avg_volume helper (pure) and the closes+volumes shape."""
from __future__ import annotations

import pytest

from gexwheel.data.prices import avg_volume


def test_avg_volume_last_window_only():
    vols = [100.0] * 10 + [200.0] * 20
    assert avg_volume(vols, 20) == 200.0


def test_avg_volume_raises_when_short():
    with pytest.raises(ValueError):
        avg_volume([1.0, 2.0], 20)
