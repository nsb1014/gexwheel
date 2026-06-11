"""Velocity spec tests. THESE PASS NOW."""
from gexwheel.analytics.velocity import mention_velocity


def test_triggers_on_3x_with_healthy_baseline():
    r = mention_velocity("SMR", 60, [20, 18, 22, 19, 21])
    assert r.triggered and r.ratio >= 3.0


def test_baseline_floor_kills_small_number_noise():
    # 2 -> 8 mentions is 4x but baseline < 10 -> no trigger
    r = mention_velocity("XYZ", 8, [2, 2, 2, 2, 2])
    assert not r.triggered


def test_insufficient_history_never_triggers():
    r = mention_velocity("NEW", 500, [10, 10])
    assert not r.triggered and r.ratio == 0.0


def test_permanent_meme_residents_ignored():
    r = mention_velocity("GME", 5000, [1500, 1400, 1600, 1500, 1500])
    assert not r.triggered  # baseline above max_daily_mentions
