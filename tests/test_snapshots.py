"""Snapshot metadata assembly (pure)."""
from conftest import make_track

from app.snapshots import build_metadata


def test_build_metadata_fields():
    t = make_track(track_id=9, cx=100, cy=200, vx=3, vy=-4, score=0.77, hits=12)
    meta = build_metadata(t, timestamp=123.0, predicted_xy=(110, 196), fired=True)
    assert meta["timestamp"] == 123.0
    assert meta["track_id"] == 9
    assert meta["score"] == 0.77
    assert meta["predicted_xy"] == [110, 196]
    assert meta["fired"] is True
    assert meta["hits"] == 12
    assert len(meta["xyxy"]) == 4


def test_build_metadata_defaults_timestamp():
    meta = build_metadata(make_track(), predicted_xy=None)
    assert isinstance(meta["timestamp"], float)
    assert meta["predicted_xy"] is None
