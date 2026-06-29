"""IoU tracker: stable ids, velocity estimation, occlusion handling."""
from conftest import make_detection

from track.tracker import IouTracker


def test_new_detection_creates_tentative_track():
    tr = IouTracker(min_hits=3)
    out = tr.update([make_detection(cx=100, cy=100)])
    assert out == []  # below min_hits -> not yet returned
    assert len(tr.tracks) == 1
    assert tr.tracks[0].id == 1
    assert tr.tracks[0].hits == 1


def test_track_confirmed_after_min_hits():
    tr = IouTracker(min_hits=3)
    for _ in range(3):
        out = tr.update([make_detection(cx=100, cy=100)])
    assert len(out) == 1
    assert out[0].id == 1
    assert out[0].hits == 3


def test_stable_id_across_frames():
    tr = IouTracker(min_hits=1)
    ids = []
    for x in (100, 105, 110, 115):
        out = tr.update([make_detection(cx=x, cy=100)])
        ids.append(out[0].id)
    assert ids == [1, 1, 1, 1]


def test_velocity_estimate_linear_motion():
    tr = IouTracker(min_hits=1, velocity_smoothing=1.0)  # exact finite difference
    out = None
    for x in (100, 110, 120, 130):
        out = tr.update([make_detection(cx=x, cy=100)])
    assert out[0].vx == 10.0  # +10 px/frame
    assert out[0].vy == 0.0


def test_two_targets_get_distinct_ids():
    tr = IouTracker(min_hits=1)
    out = tr.update([make_detection(cx=100, cy=100),
                     make_detection(cx=500, cy=500)])
    assert {t.id for t in out} == {1, 2}


def test_ids_persist_when_targets_move_apart():
    tr = IouTracker(min_hits=1)
    tr.update([make_detection(cx=100, cy=100), make_detection(cx=500, cy=500)])
    out = tr.update([make_detection(cx=110, cy=100), make_detection(cx=490, cy=500)])
    by_id = {t.id: t for t in out}
    assert by_id[1].cx == 110  # track 1 followed the left object
    assert by_id[2].cx == 490  # track 2 followed the right object


def test_lost_track_coasts_then_is_dropped():
    tr = IouTracker(min_hits=1, max_age_frames=2)
    tr.update([make_detection(cx=100, cy=100)])
    out = tr.update([])                  # miss 1: still coasting -> returned
    assert len(out) == 1 and out[0].time_since_update == 1
    tr.update([])                        # miss 2 (time_since_update == max_age, kept)
    assert len(tr.tracks) == 1
    out = tr.update([])                  # miss 3 -> exceeds max_age, dropped
    assert tr.tracks == []
    assert out == []


def test_reacquire_keeps_predicting_velocity_across_gap():
    tr = IouTracker(min_hits=1, max_age_frames=5, velocity_smoothing=1.0)
    tr.update([make_detection(cx=100, cy=100)])
    tr.update([make_detection(cx=110, cy=100)])   # vx = 10
    tr.update([])                                  # 1-frame occlusion
    out = tr.update([make_detection(cx=130, cy=100)])  # moved 20 over 2 frames
    assert out[0].vx == 10.0  # (130-110)/2 frames


def test_apply_config_updates_params_live():
    from config import Config
    t = IouTracker(0.3, 30, 3, 0.5)
    cfg = Config()
    cfg.tracker.iou_match_threshold = 0.6
    cfg.tracker.max_age_frames = 12
    cfg.tracker.min_hits = 5
    cfg.tracker.velocity_smoothing = 0.2
    t.apply_config(cfg.tracker)
    assert (t.iou_threshold, t.max_age_frames, t.min_hits, t.alpha) == (0.6, 12, 5, 0.2)
