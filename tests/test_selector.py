"""Target selection + anti-thrash hysteresis."""
from strategy.selector import TargetSelector


def test_empty_returns_none():
    sel = TargetSelector()
    assert sel.select([]) is None
    assert sel.current_id is None


def test_picks_highest_first():
    sel = TargetSelector()
    assert sel.select([(1, 0.4), (2, 0.9), (3, 0.5)]) == 2


def test_holds_target_when_still_best():
    sel = TargetSelector(switch_hysteresis=0.15, min_target_dwell_frames=1)
    sel.select([(1, 0.9), (2, 0.4)])
    assert sel.select([(1, 0.8), (2, 0.5)]) == 1


def test_no_thrash_within_hysteresis_margin():
    sel = TargetSelector(switch_hysteresis=0.15, min_target_dwell_frames=1)
    sel.select([(1, 0.80), (2, 0.50)])         # pick 1
    # rival 2 now leads but only by 0.10 < 0.15 -> hold 1
    assert sel.select([(1, 0.60), (2, 0.70)]) == 1


def test_switches_past_hysteresis_after_min_dwell():
    sel = TargetSelector(switch_hysteresis=0.15, min_target_dwell_frames=2)
    sel.select([(1, 0.80), (2, 0.40)])         # frame 1, pick 1, dwell=1
    sel.select([(1, 0.80), (2, 0.40)])         # frame 2, dwell=2
    # rival 2 leads by 0.30 > 0.15 and dwell met -> switch
    assert sel.select([(1, 0.40), (2, 0.80)]) == 2


def test_min_dwell_blocks_early_switch():
    sel = TargetSelector(switch_hysteresis=0.15, min_target_dwell_frames=5)
    sel.select([(1, 0.80), (2, 0.40)])         # pick 1, dwell=1
    # rival 2 leads hugely but dwell (1) < min_dwell (5) -> hold 1
    assert sel.select([(1, 0.10), (2, 0.95)]) == 1


def test_switches_immediately_when_current_disappears():
    sel = TargetSelector(min_target_dwell_frames=10)
    sel.select([(1, 0.9), (2, 0.4)])           # pick 1
    assert sel.select([(2, 0.4), (3, 0.3)]) == 2  # 1 gone -> take best remaining
