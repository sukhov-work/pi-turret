"""Per-track scoring: features behave and weights are honored."""
from dataclasses import replace

from conftest import make_track

from config import KillZoneConfig, StrategyConfig
from strategy.scoring import score_track

KZ = KillZoneConfig(cx_px=576, cy_px=576, half_w_px=120, half_h_px=120)
CFG = StrategyConfig()
FRAME = (1152.0, 1152.0)


def _score(track, cfg=CFG):
    return score_track(track, KZ, cfg, *FRAME)


def test_closer_to_killzone_scores_higher():
    near = make_track(cx=576, cy=576)
    far = make_track(cx=50, cy=50)
    assert _score(near) > _score(far)


def test_moving_toward_killzone_beats_moving_away():
    toward = make_track(cx=200, cy=576, vx=20, vy=0)   # +x toward center (576)
    away = make_track(cx=200, cy=576, vx=-20, vy=0)    # -x away from center
    assert _score(toward) > _score(away)


def test_bigger_target_scores_higher():
    big = make_track(cx=300, cy=300, w=200, h=200)
    small = make_track(cx=300, cy=300, w=20, h=20)
    assert _score(big) > _score(small)


def test_higher_confidence_scores_higher():
    hi = make_track(cx=300, cy=300, score=0.95)
    lo = make_track(cx=300, cy=300, score=0.30)
    assert _score(hi) > _score(lo)


def test_zero_weight_removes_feature_influence():
    # With only killzone weight, two tracks equidistant but different size tie.
    cfg = StrategyConfig(w_killzone=1.0, w_size=0.0, w_dwell=0.0,
                         w_approach=0.0, w_confidence=0.0)
    a = make_track(cx=576, cy=576, w=200, h=200, score=0.9, hits=20)
    b = make_track(cx=576, cy=576, w=10, h=10, score=0.3, hits=1)
    assert _score(a, cfg) == _score(b, cfg)


def test_score_in_unit_range():
    t = make_track(cx=576, cy=576, w=300, h=300, vx=30, vy=0, score=1.0, hits=99)
    s = _score(t)
    assert 0.0 <= s <= 1.0
