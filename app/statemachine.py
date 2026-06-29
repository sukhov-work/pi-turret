"""Fire control state machine — non-blocking, fail-safe.

States: SEARCHING -> TRACKING -> AIMING -> FIRING -> COOLDOWN -> SEARCHING, plus
SAFE (entered on any error / disarm). Firing is **non-blocking**: entering FIRING
calls ``on_fire`` once; the machine polls an injected clock and calls ``off_fire``
exactly once when the duration elapses (or the target is lost mid-shot), then
debounces in COOLDOWN. There is no ``time.sleep`` anywhere.

``fire.enabled = False`` keeps the machine in AIMING but still reports
``last_would_fire`` — the "would-fire" telemetry mode for safe demos.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from config import FireConfig


class FireState(Enum):
    SEARCHING = "searching"
    TRACKING = "tracking"
    AIMING = "aiming"
    FIRING = "firing"
    COOLDOWN = "cooldown"
    SAFE = "safe"


@dataclass
class FireContext:
    has_target: bool = False
    aim_error_px: float = 1e9
    predicted_in_killzone: bool = False


def _default_clock() -> float:
    import time
    return time.monotonic()


class FireStateMachine:
    def __init__(self, cfg: FireConfig,
                 clock: Callable[[], float] = _default_clock,
                 on_fire: Optional[Callable[[], None]] = None,
                 off_fire: Optional[Callable[[], None]] = None):
        self.cfg = cfg
        self._clock = clock
        self._on_fire = on_fire or (lambda: None)
        self._off_fire = off_fire or (lambda: None)
        self.state = FireState.SEARCHING
        self.last_would_fire = False
        self._fire_start = 0.0
        self._cooldown_start = 0.0

    def reset(self) -> None:
        self._off_fire()
        self.state = FireState.SEARCHING
        self.last_would_fire = False

    def enter_safe(self) -> None:
        """Disarm: pump off, hold SAFE until ``reset``."""
        self._off_fire()
        self.state = FireState.SAFE
        self.last_would_fire = False

    def step(self, ctx: FireContext) -> FireState:
        now = self._clock()

        if self.state is FireState.SAFE:
            return self.state

        if self.state is FireState.FIRING:
            elapsed = now - self._fire_start
            if elapsed >= self.cfg.fire_duration_s or not ctx.has_target:
                self._exit_firing(now)
            return self.state

        if self.state is FireState.COOLDOWN:
            if now - self._cooldown_start >= self.cfg.cooldown_s:
                self.state = FireState.SEARCHING
            return self.state

        # Acquisition path: SEARCHING / TRACKING / AIMING.
        if not ctx.has_target:
            self.state = FireState.SEARCHING
            self.last_would_fire = False
            return self.state

        if self.state is FireState.SEARCHING:
            self.state = FireState.TRACKING
        if self.state is FireState.TRACKING:
            self.state = FireState.AIMING

        should = self._should_fire(ctx)
        self.last_would_fire = should
        if should and self.cfg.enabled:
            self._enter_firing(now)
        return self.state

    def _should_fire(self, ctx: FireContext) -> bool:
        in_kz = ctx.predicted_in_killzone or not self.cfg.require_killzone
        aim_ok = abs(ctx.aim_error_px) <= self.cfg.aim_deadband_px
        return in_kz and aim_ok

    def _enter_firing(self, now: float) -> None:
        self._on_fire()
        self._fire_start = now
        self.state = FireState.FIRING

    def _exit_firing(self, now: float) -> None:
        self._off_fire()                 # pump OFF on every exit from FIRING
        self._cooldown_start = now
        self.state = FireState.COOLDOWN
