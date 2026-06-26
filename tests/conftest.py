"""Shared pytest fixtures + factory helpers. Mock the hardware, never the logic."""
from __future__ import annotations

from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import numpy as np
import pytest

from contracts import Detection, Track


# ---- Factory helpers (avoid hand-building dataclasses in every test) ----

def make_detection(cx: float = 100.0, cy: float = 100.0, w: float = 40.0,
                   h: float = 40.0, score: float = 0.9, cls_id: int = 0) -> Detection:
    return Detection.from_xyxy(cls_id, score, cx - w / 2, cy - h / 2,
                               cx + w / 2, cy + h / 2)


def make_track(track_id: int = 1, cx: float = 100.0, cy: float = 100.0,
               w: float = 40.0, h: float = 40.0, vx: float = 0.0, vy: float = 0.0,
               score: float = 0.9, cls_id: int = 0, hits: int = 5,
               time_since_update: int = 0) -> Track:
    return Track(
        id=track_id, cls_id=cls_id, score=score,
        xyxy=(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
        cx=cx, cy=cy, vx=vx, vy=vy, age=hits, hits=hits,
        time_since_update=time_since_update,
    )


@pytest.fixture
def detection_factory():
    return make_detection


@pytest.fixture
def track_factory():
    return make_track


# ---- Hardware fakes ----

@pytest.fixture
def fake_frame():
    return np.zeros((256, 256), dtype=np.uint8)


@pytest.fixture
def fake_servo_bus():
    """Records (channel, pulse_us) writes so tests can assert clamping + order."""
    bus = MagicMock()
    bus.writes: List[Tuple[int, float]] = []
    bus.set_servo_pulse.side_effect = lambda ch, us: bus.writes.append((ch, us))
    return bus


@pytest.fixture
def fake_pump_device():
    """A stand-in for a gpiozero OutputDevice; records on/off transitions."""
    dev = MagicMock()
    dev.events: List[str] = []
    dev.on.side_effect = lambda: dev.events.append("on")
    dev.off.side_effect = lambda: dev.events.append("off")
    return dev


class FakeClock:
    """Manually-advanced monotonic clock for state-machine timing tests."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def fake_clock():
    return FakeClock()
