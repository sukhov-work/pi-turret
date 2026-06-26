"""Constant-velocity lead prediction (pure logic).

Predict where a track's centroid will be after the actuation horizon
(servo travel + water time-of-flight), so the turret aims where the bird *will*
be, not where it was. A brand-new track has no velocity yet, so it degrades to
"aim at the current centroid" (lead = 0). Seam left for constant-acceleration.
"""
from __future__ import annotations

from typing import Tuple

from contracts import Track


def lead_frames_from_seconds(seconds: float, fps: float) -> float:
    """Convert a time horizon to a frame count given the capture rate."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    return seconds * fps


def predict_position(track: Track, lead_frames: float) -> Tuple[float, float]:
    """Centroid extrapolated ``lead_frames`` ahead at constant velocity."""
    return (track.cx + track.vx * lead_frames,
            track.cy + track.vy * lead_frames)


def predict_lead(track: Track, lead_time_s: float, fps: float) -> Tuple[float, float]:
    """Predicted centroid after ``lead_time_s`` seconds."""
    return predict_position(track, lead_frames_from_seconds(lead_time_s, fps))
