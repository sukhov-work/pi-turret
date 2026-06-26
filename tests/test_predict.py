"""Constant-velocity lead predictor."""
import pytest

from conftest import make_track

from track.predict import lead_frames_from_seconds, predict_lead, predict_position


def test_predict_position_linear():
    t = make_track(cx=100, cy=200, vx=10, vy=-5)
    px, py = predict_position(t, lead_frames=3)
    assert (px, py) == (130, 185)


def test_new_track_no_lead():
    t = make_track(cx=100, cy=200, vx=0, vy=0)
    assert predict_position(t, lead_frames=10) == (100, 200)


def test_lead_frames_from_seconds():
    assert lead_frames_from_seconds(0.5, fps=20) == 10.0


def test_lead_frames_rejects_bad_fps():
    with pytest.raises(ValueError):
        lead_frames_from_seconds(0.5, fps=0)


def test_predict_lead_seconds():
    t = make_track(cx=0, cy=0, vx=20, vy=0)
    px, py = predict_lead(t, lead_time_s=0.5, fps=20)  # 10 frames
    assert (px, py) == (200, 0)
