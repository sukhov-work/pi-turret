"""Fire state machine: transitions, non-blocking timing, pump-off discipline."""
import pytest

from config import FireConfig
from app.statemachine import FireContext, FireState, FireStateMachine


def _sm(fake_clock, enabled=True, **overrides):
    cfg = FireConfig(enabled=enabled, fire_duration_s=1.0, cooldown_s=2.0,
                     aim_deadband_px=12.0, require_killzone=True, **overrides)
    events = []
    sm = FireStateMachine(
        cfg, clock=fake_clock,
        on_fire=lambda: events.append("on"),
        off_fire=lambda: events.append("off"),
    )
    return sm, events


def _ready_ctx():
    return FireContext(has_target=True, aim_error_px=0.0, predicted_in_killzone=True)


def test_acquires_target_to_aiming(fake_clock):
    sm, _ = _sm(fake_clock)
    st = sm.step(FireContext(has_target=True, aim_error_px=500.0))
    assert st is FireState.AIMING  # cascades SEARCHING->TRACKING->AIMING


def test_no_target_returns_to_searching(fake_clock):
    sm, _ = _sm(fake_clock)
    sm.step(FireContext(has_target=True, aim_error_px=500.0))
    assert sm.step(FireContext(has_target=False)) is FireState.SEARCHING


def test_fires_when_predicate_met(fake_clock):
    sm, events = _sm(fake_clock)
    assert sm.step(_ready_ctx()) is FireState.FIRING
    assert events == ["on"]


def test_firing_is_non_blocking_then_cooldown(fake_clock):
    sm, events = _sm(fake_clock)
    sm.step(_ready_ctx())                 # -> FIRING at t=0
    fake_clock.advance(0.5)
    assert sm.step(_ready_ctx()) is FireState.FIRING   # still firing mid-duration
    assert events == ["on"]
    fake_clock.advance(0.6)               # t=1.1 >= duration 1.0
    assert sm.step(_ready_ctx()) is FireState.COOLDOWN
    assert events == ["on", "off"]        # pump OFF exactly once


def test_losing_target_mid_fire_turns_pump_off(fake_clock):
    sm, events = _sm(fake_clock)
    sm.step(_ready_ctx())                 # FIRING
    st = sm.step(FireContext(has_target=False))   # target lost before duration
    assert st is FireState.COOLDOWN
    assert events == ["on", "off"]


def test_cooldown_blocks_immediate_refire(fake_clock):
    sm, events = _sm(fake_clock)
    sm.step(_ready_ctx())
    fake_clock.advance(1.0)
    sm.step(_ready_ctx())                 # -> COOLDOWN at t=1.0
    # still within cooldown: cannot fire again
    fake_clock.advance(1.0)              # t=2.0 < cooldown_start(1.0)+2.0
    assert sm.step(_ready_ctx()) is FireState.COOLDOWN
    assert events.count("on") == 1
    fake_clock.advance(1.1)             # t=3.1 >= 3.0 -> cooldown done
    assert sm.step(_ready_ctx()) is FireState.SEARCHING   # leaves cooldown first
    assert sm.step(_ready_ctx()) is FireState.FIRING      # then re-acquires + fires
    assert events.count("on") == 2


def test_disabled_reports_would_fire_but_never_actuates(fake_clock):
    sm, events = _sm(fake_clock, enabled=False)
    st = sm.step(_ready_ctx())
    assert st is FireState.AIMING
    assert sm.last_would_fire is True
    assert events == []                   # on_fire never called


def test_aim_error_outside_deadband_holds_fire(fake_clock):
    sm, events = _sm(fake_clock)
    st = sm.step(FireContext(has_target=True, aim_error_px=50.0,
                             predicted_in_killzone=True))
    assert st is FireState.AIMING
    assert sm.last_would_fire is False
    assert events == []


def test_outside_killzone_holds_fire(fake_clock):
    sm, events = _sm(fake_clock)
    st = sm.step(FireContext(has_target=True, aim_error_px=0.0,
                             predicted_in_killzone=False))
    assert st is FireState.AIMING
    assert events == []


def test_enter_safe_and_reset(fake_clock):
    sm, events = _sm(fake_clock)
    sm.step(_ready_ctx())                 # FIRING (on)
    sm.enter_safe()
    assert sm.state is FireState.SAFE
    assert events[-1] == "off"            # disarm turned the pump off
    sm.reset()
    assert sm.state is FireState.SEARCHING
