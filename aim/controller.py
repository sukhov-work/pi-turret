"""Closed-loop aim controller: pixel error -> angle delta (pure logic).

Replaces v1's open-loop single-shot move. A P (optionally PI) step converts the
pixel error to a small per-tick angle delta, with a deadband (don't chase noise),
a per-tick slew cap (limit current spikes / overshoot), and anti-windup on the
integral. The final approach is taken from one fixed direction to absorb MG996R
backlash consistently (``one_directional_target``).

This module is pure: settling time and real backlash behaviour are Pi-only truth.
"""
from __future__ import annotations


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


class PIController:
    def __init__(self, kp: float, ki: float = 0.0, deadband_px: float = 0.0,
                 max_step_deg: float = 1e9, integral_limit_deg: float = 1e9):
        self.kp = kp
        self.ki = ki
        self.deadband_px = deadband_px
        self.max_step_deg = max_step_deg
        self.integral_limit_deg = integral_limit_deg
        self._integral = 0.0

    def reset(self) -> None:
        self._integral = 0.0

    def step(self, error_px: float, dt: float = 1.0) -> float:
        """Angle delta (deg) to apply this tick for the given pixel error."""
        if abs(error_px) <= self.deadband_px:
            self._integral = 0.0   # at target: shed accumulated integral
            return 0.0

        proportional = self.kp * error_px

        integral_term = 0.0
        if self.ki > 0.0:
            self._integral += error_px * dt
            # Anti-windup: clamp the integral so its contribution stays bounded.
            limit = self.integral_limit_deg / self.ki
            self._integral = _clamp(self._integral, -limit, limit)
            integral_term = self.ki * self._integral

        return _clamp(proportional + integral_term,
                      -self.max_step_deg, self.max_step_deg)


def slew_toward(current_deg: float, target_deg: float, max_step_deg: float) -> float:
    """Move ``current_deg`` toward ``target_deg`` by at most ``max_step_deg``.

    The per-tick rate limit keeps moves smooth and bounds current spikes instead
    of v1's single jump. Re-commanding the same angle is idempotent.
    """
    delta = _clamp(target_deg - current_deg, -max_step_deg, max_step_deg)
    return current_deg + delta


def one_directional_target(target_deg: float, prev_deg: float,
                           takeup_deg: float, approach_dir: int = 1) -> float:
    """Bias the command so the final approach always comes from one side.

    With ``approach_dir == +1`` the servo should settle while moving in the +deg
    direction. When the requested move is *against* that direction, undershoot by
    ``takeup_deg`` so subsequent ticks approach the target from below, taking up
    gear backlash consistently. (Physical effect is Pi-verified.)
    """
    if approach_dir >= 0:
        return target_deg if target_deg >= prev_deg else target_deg - takeup_deg
    return target_deg if target_deg <= prev_deg else target_deg + takeup_deg
