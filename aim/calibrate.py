"""Pixel<->angle calibration (pure-logic apply + fit).

Replaces v1's hand-tuned ÷25/÷15 coefficients with a fitted affine transform per
axis: ``deg = a*cx + b*cy + c``. The *fit* (collecting pixel<->angle samples) needs
the real rig and runs on the Pi; the *apply* and the least-squares solve are pure
logic and unit-tested on the Mac. Parallax (camera vs nozzle) and water-drop
aim-above are separate additive offsets.

Returned angles are raw — the servo layer clamps them to the mechanical envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np

from config import AimConfig

Coeffs = Tuple[float, float, float]


@dataclass
class Calibration:
    pan_coeffs: Coeffs    # (a, b, c) for pan_deg = a*cx + b*cy + c
    tilt_coeffs: Coeffs

    @classmethod
    def from_config(cls, cfg: AimConfig) -> "Calibration":
        return cls(tuple(cfg.pan_coeffs), tuple(cfg.tilt_coeffs))


def apply_calibration(cal: Calibration, cx: float, cy: float) -> Tuple[float, float]:
    """Map a full-frame pixel to a (pan_deg, tilt_deg) aim point (unclamped)."""
    pa, pb, pc = cal.pan_coeffs
    ta, tb, tc = cal.tilt_coeffs
    pan_deg = pa * cx + pb * cy + pc
    tilt_deg = ta * cx + tb * cy + tc
    return pan_deg, tilt_deg


def apply_aim_offsets(pan_deg: float, tilt_deg: float,
                      parallax_pan_deg: float = 0.0,
                      drop_tilt_deg: float = 0.0) -> Tuple[float, float]:
    """Apply camera/nozzle parallax (pan) and water-drop aim-above (tilt)."""
    return pan_deg + parallax_pan_deg, tilt_deg + drop_tilt_deg


def fit_calibration(pixels: Sequence[Tuple[float, float]],
                    pan_angles: Sequence[float],
                    tilt_angles: Sequence[float]) -> Calibration:
    """Least-squares fit of the per-axis affine transform from samples.

    ``pixels`` are (cx, cy) of a fixed target observed at known servo angles.
    Needs >= 3 non-collinear samples per axis.
    """
    if not (len(pixels) == len(pan_angles) == len(tilt_angles)):
        raise ValueError("pixels, pan_angles, tilt_angles must be equal length")
    if len(pixels) < 3:
        raise ValueError("need at least 3 calibration samples")

    A = np.array([[cx, cy, 1.0] for cx, cy in pixels], dtype=np.float64)
    pan = np.asarray(pan_angles, dtype=np.float64)
    tilt = np.asarray(tilt_angles, dtype=np.float64)

    pan_sol, *_ = np.linalg.lstsq(A, pan, rcond=None)
    tilt_sol, *_ = np.linalg.lstsq(A, tilt, rcond=None)
    return Calibration(
        pan_coeffs=(float(pan_sol[0]), float(pan_sol[1]), float(pan_sol[2])),
        tilt_coeffs=(float(tilt_sol[0]), float(tilt_sol[1]), float(tilt_sol[2])),
    )
