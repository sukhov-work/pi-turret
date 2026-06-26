"""Pixel<->angle calibration: apply, fit, and the v1 preset."""
import pytest

from config import AimConfig
from aim.calibrate import (
    Calibration,
    apply_aim_offsets,
    apply_calibration,
    fit_calibration,
)


def test_apply_matches_manual():
    cal = Calibration(pan_coeffs=(-0.04, 0.0, 59.04), tilt_coeffs=(0.0, 1 / 15.0, -10.4))
    pan, tilt = apply_calibration(cal, cx=576, cy=576)
    assert pan == pytest.approx(36.0)   # -0.04*576 + 59.04
    assert tilt == pytest.approx(28.0)  # 576/15 - 10.4


def test_v1_preset_reproduces_v1_center_aim():
    # v1 pointAndFire at frame center (576,576): pan 31+5=36, tilt 23+5=28.
    cal = Calibration.from_config(AimConfig())
    pan, tilt = apply_calibration(cal, 576, 576)
    assert pan == pytest.approx(36.0)
    assert tilt == pytest.approx(28.0)


def test_fit_recovers_known_affine():
    # ground-truth transform
    pan_c = (0.05, -0.01, 10.0)
    tilt_c = (0.0, 0.04, -3.0)
    pixels = [(0, 0), (100, 0), (0, 100), (200, 50), (50, 200)]
    pan = [pan_c[0] * x + pan_c[1] * y + pan_c[2] for x, y in pixels]
    tilt = [tilt_c[0] * x + tilt_c[1] * y + tilt_c[2] for x, y in pixels]
    cal = fit_calibration(pixels, pan, tilt)
    for got, want in zip(cal.pan_coeffs, pan_c):
        assert got == pytest.approx(want, abs=1e-6)
    for got, want in zip(cal.tilt_coeffs, tilt_c):
        assert got == pytest.approx(want, abs=1e-6)


def test_fit_rejects_too_few_samples():
    with pytest.raises(ValueError):
        fit_calibration([(0, 0), (1, 1)], [0, 1], [0, 1])


def test_aim_offsets_add():
    pan, tilt = apply_aim_offsets(30.0, 20.0, parallax_pan_deg=2.0, drop_tilt_deg=-1.5)
    assert (pan, tilt) == (32.0, 18.5)
