"""Per-track threat/priority scoring — a tunable weighted sum (pure logic).

Score is normalized to [0, 1] (weighted features / total weight) so the selector's
``switch_hysteresis`` reads as a fraction. All weights live in ``StrategyConfig``;
set a weight to 0 to drop a feature without touching code.
"""
from __future__ import annotations

import math
from typing import Tuple

from config import KillZoneConfig, StrategyConfig
from contracts import Track


def _killzone_center(kz: KillZoneConfig) -> Tuple[float, float]:
    return (kz.cx_px, kz.cy_px)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def score_track(track: Track, kz: KillZoneConfig, cfg: StrategyConfig,
                frame_width_px: float, frame_height_px: float) -> float:
    """Return a priority score in [0, 1]. Higher = engage sooner."""
    frame_diag = math.hypot(frame_width_px, frame_height_px)
    kzx, kzy = _killzone_center(kz)

    dx = kzx - track.cx
    dy = kzy - track.cy
    dist = math.hypot(dx, dy)

    # 1. Proximity to kill-zone center.
    killzone_prox = _clamp01(1.0 - dist / frame_diag)

    # 2. Size / closeness (box diagonal relative to frame diagonal).
    x1, y1, x2, y2 = track.xyxy
    box_diag = math.hypot(x2 - x1, y2 - y1)
    size = _clamp01(box_diag / frame_diag)

    # 3. Dwell (persistence).
    dwell = _clamp01(track.hits / float(max(1, cfg.dwell_norm_frames)))

    # 4. Approach: cosine of velocity vs the unit vector toward the kill-zone.
    speed = math.hypot(track.vx, track.vy)
    if speed > 1e-6 and dist > 1e-6:
        approach = _clamp01((track.vx * dx + track.vy * dy) / (speed * dist))
    else:
        approach = 0.0

    # 5. Detector confidence.
    confidence = _clamp01(track.score)

    weighted = (
        cfg.w_killzone * killzone_prox
        + cfg.w_size * size
        + cfg.w_dwell * dwell
        + cfg.w_approach * approach
        + cfg.w_confidence * confidence
    )
    total = cfg.w_killzone + cfg.w_size + cfg.w_dwell + cfg.w_approach + cfg.w_confidence
    return weighted / total if total > 0 else 0.0
