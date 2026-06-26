"""Kill-zone geometry — the trip-wire fire gate (pure logic).

Tracking and scoring run full-frame; firing is gated on the *predicted* position
entering this zone. Supports a rectangular or circular zone.
"""
from __future__ import annotations

import math

from config import KillZoneConfig


def is_in_kill_zone(px: float, py: float, kz: KillZoneConfig) -> bool:
    if kz.shape == "circle":
        return math.hypot(px - kz.cx_px, py - kz.cy_px) <= kz.radius_px
    return (abs(px - kz.cx_px) <= kz.half_w_px
            and abs(py - kz.cy_px) <= kz.half_h_px)


def distance_to_center_px(px: float, py: float, kz: KillZoneConfig) -> float:
    return math.hypot(px - kz.cx_px, py - kz.cy_px)
