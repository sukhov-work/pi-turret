"""P/PI controller step: deadband, slew cap, anti-windup, convergence."""
import pytest

from aim.controller import PIController, one_directional_target


def test_deadband_returns_zero():
    c = PIController(kp=0.05, deadband_px=10.0)
    assert c.step(5.0) == 0.0
    assert c.step(-9.9) == 0.0


def test_proportional_output():
    c = PIController(kp=0.02, deadband_px=0.0, max_step_deg=1e9)
    assert c.step(100.0) == pytest.approx(2.0)
    assert c.step(-100.0) == pytest.approx(-2.0)


def test_output_clamped_to_max_step():
    c = PIController(kp=1.0, deadband_px=0.0, max_step_deg=6.0)
    assert c.step(100.0) == 6.0
    assert c.step(-100.0) == -6.0


def test_integral_accumulates_and_is_windup_limited():
    c = PIController(kp=0.0, ki=0.1, deadband_px=0.0,
                     max_step_deg=1e9, integral_limit_deg=5.0)
    out = 0.0
    for _ in range(100):
        out = c.step(50.0)
    # ki*integral is clamped so the integral term never exceeds the limit
    assert out == pytest.approx(5.0)


def test_deadband_resets_integral():
    c = PIController(kp=0.0, ki=0.1, deadband_px=5.0)
    c.step(50.0)
    c.step(50.0)
    assert c.step(1.0) == 0.0   # inside deadband
    # integral was shed, so a fresh error starts the term from ~0
    assert c.step(50.0) == pytest.approx(0.1 * 50.0)


def test_closed_loop_converges_without_overshoot():
    # Plant: each applied degree reduces pixel error by `px_per_deg`.
    c = PIController(kp=0.05, deadband_px=1.0, max_step_deg=20.0)
    px_per_deg = 10.0
    error = 200.0
    prev_error = error
    for _ in range(100):
        delta = c.step(error)
        error -= delta * px_per_deg
        # never crosses zero (no overshoot) with this conservative gain
        assert error <= prev_error + 1e-9
        prev_error = error
        if abs(error) <= 1.0:
            break
    assert abs(error) <= 1.0


def test_one_directional_takeup_same_side():
    # approach from +: a forward move is direct; a backward move undershoots.
    assert one_directional_target(30.0, prev_deg=20.0, takeup_deg=1.0) == 30.0
    assert one_directional_target(20.0, prev_deg=30.0, takeup_deg=1.0) == 19.0


def test_one_directional_takeup_negative_dir():
    assert one_directional_target(20.0, prev_deg=30.0, takeup_deg=1.0,
                                  approach_dir=-1) == 20.0
    assert one_directional_target(30.0, prev_deg=20.0, takeup_deg=1.0,
                                  approach_dir=-1) == 31.0
