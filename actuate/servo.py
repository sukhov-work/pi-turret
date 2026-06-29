"""ServoController: the single owner of servo motion, with safety clamps.

Clamps BOTH angle (to the mechanical envelope) and pulse (to the absolute guard)
on **every** write — never trusts an upstream angle. Holds a lock so manual jog
and auto-track can't drive the servos at once. Any driver failure raises
``ServoError`` for the control loop to handle by disarming.

Angle<->pulse mapping is v1's: ``pulse_us = deg * slope + offset``.
"""
from __future__ import annotations

import threading
from enum import Enum
from typing import Any

from config import ServoConfig
from errors import ServoError


class Axis(Enum):
    PAN = "pan"
    TILT = "tilt"


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


class ServoController:
    def __init__(self, driver: Any, cfg: ServoConfig):
        self._driver = driver
        self._cfg = cfg
        self._lock = threading.Lock()
        self._last_deg = {Axis.PAN: cfg.home_pan_deg, Axis.TILT: cfg.home_tilt_deg}

    def apply_config(self, cfg: ServoConfig) -> None:
        """Adopt a new servo config live (clamps / home / pulse mapping / channels).

        Held under the lock so an in-flight write never sees a torn config. i2c bus/
        address and pwm freq only matter at driver ``setup()`` — those need a restart.
        """
        with self._lock:
            self._cfg = cfg

    def _channel(self, axis: Axis) -> int:
        return self._cfg.pan_channel if axis is Axis.PAN else self._cfg.tilt_channel

    def _angle_limits(self, axis: Axis):
        if axis is Axis.PAN:
            return self._cfg.pan_min_deg, self._cfg.pan_max_deg
        return self._cfg.tilt_min_deg, self._cfg.tilt_max_deg

    def angle_to_pulse_us(self, deg: float) -> float:
        return deg * self._cfg.pulse_slope_us_per_deg + self._cfg.pulse_offset_us

    def clamp_angle(self, axis: Axis, deg: float) -> float:
        lo, hi = self._angle_limits(axis)
        return _clamp(deg, lo, hi)

    def set_angle(self, axis: Axis, deg: float) -> float:
        """Clamp, map to a pulse, clamp the pulse, and write. Returns the angle used."""
        with self._lock:
            clamped_deg = self.clamp_angle(axis, deg)
            pulse_us = _clamp(self.angle_to_pulse_us(clamped_deg),
                              self._cfg.pulse_min_us, self._cfg.pulse_max_us)
            try:
                self._driver.set_servo_pulse(self._channel(axis), pulse_us)
            except Exception as exc:  # noqa: BLE001 — surface as safety-critical
                raise ServoError(
                    f"servo write failed (axis={axis.value} deg={clamped_deg:.1f} "
                    f"pulse={pulse_us:.0f}us)"
                ) from exc
            self._last_deg[axis] = clamped_deg
            return clamped_deg

    def last_angle(self, axis: Axis) -> float:
        return self._last_deg[axis]

    def center(self) -> None:
        self.set_angle(Axis.PAN, self._cfg.home_pan_deg)
        self.set_angle(Axis.TILT, self._cfg.home_tilt_deg)

    def disarm(self) -> None:
        """Safe state: center, then relax both servos (stop driving)."""
        try:
            self.center()
        finally:
            relax = getattr(self._driver, "relax", None)
            if callable(relax):
                with self._lock:
                    relax(self._cfg.pan_channel)
                    relax(self._cfg.tilt_channel)
