"""Integration: ControlLoop wiring honors clamps + fire predicate (Mac, fakes)."""
import pytest

from conftest import make_track

from actuate.servo import Axis, ServoController
from app.control import ControlLoop
from app.statemachine import FireState, FireStateMachine
from config import Config
from strategy.selector import TargetSelector


class FakeDriver:
    def __init__(self):
        self.writes = []

    def set_servo_pulse(self, channel, pulse_us):
        self.writes.append((channel, pulse_us))

    def relax(self, channel):
        pass


def _loop(enabled=True):
    cfg = Config()
    cfg.fire.enabled = enabled
    cfg.fire.aim_deadband_px = 20.0
    drv = FakeDriver()
    servo = ServoController(drv, cfg.servo)
    events = []
    sm = FireStateMachine(cfg.fire, on_fire=lambda: events.append("on"),
                          off_fire=lambda: events.append("off"))
    selector = TargetSelector(cfg.strategy.switch_hysteresis, min_target_dwell_frames=1)
    loop = ControlLoop(cfg, servo, selector, sm)
    return cfg, loop, drv, servo, events


def test_no_tracks_searches_and_does_not_move_servo():
    cfg, loop, drv, servo, events = _loop()
    tel = loop.tick([])
    assert tel.state is FireState.SEARCHING
    assert tel.selected_target_id is None
    assert drv.writes == []  # nothing commanded


def test_target_in_killzone_fires_when_enabled():
    cfg, loop, drv, servo, events = _loop(enabled=True)
    # zero-velocity track sitting at the kill-zone center -> predicted there too
    target = make_track(track_id=7, cx=cfg.killzone.cx_px, cy=cfg.killzone.cy_px)
    tel = loop.tick([target])
    assert tel.selected_target_id == 7
    assert tel.in_killzone is True
    assert tel.aim_error_px == pytest.approx(0.0)
    assert tel.state is FireState.FIRING
    assert events == ["on"]


def test_disabled_reports_would_fire_only():
    cfg, loop, drv, servo, events = _loop(enabled=False)
    target = make_track(track_id=1, cx=cfg.killzone.cx_px, cy=cfg.killzone.cy_px)
    tel = loop.tick([target])
    assert tel.would_fire is True
    assert tel.state is FireState.AIMING
    assert events == []


def test_servo_writes_stay_within_clamps():
    cfg, loop, drv, servo, events = _loop()
    # a far off-center target drives the servo, but it must stay within the guard
    target = make_track(track_id=1, cx=0, cy=0, vx=0, vy=0)
    for _ in range(10):
        loop.tick([target])
    for ch, pulse in drv.writes:
        assert cfg.servo.pulse_min_us <= pulse <= cfg.servo.pulse_max_us
        assert ch in (cfg.servo.pan_channel, cfg.servo.tilt_channel)


def test_servo_slews_at_most_max_step_per_tick():
    cfg, loop, drv, servo, events = _loop()
    target = make_track(track_id=1, cx=0, cy=0)  # calibration target far from home
    prev = servo.last_angle(Axis.PAN)
    for _ in range(5):
        loop.tick([target])
        now = servo.last_angle(Axis.PAN)
        assert abs(now - prev) <= cfg.controller.max_step_deg + 1e-9
        prev = now


class _FakeIndicator:
    def __init__(self):
        self.state = None

    def set(self, value):
        self.state = value


def test_status_led_tracks_armed_state():
    cfg = Config()
    cfg.fire.enabled = True
    cfg.fire.aim_deadband_px = 20.0
    drv = FakeDriver()
    servo = ServoController(drv, cfg.servo)
    sm = FireStateMachine(cfg.fire)
    status = _FakeIndicator()
    loop = ControlLoop(cfg, servo, TargetSelector(min_target_dwell_frames=1), sm,
                       status_led=status)
    loop.tick([make_track(track_id=1, cx=cfg.killzone.cx_px, cy=cfg.killzone.cy_px)])
    assert status.state is True            # active -> status LED on
    sm.enter_safe()
    loop.tick([])
    assert status.state is False           # SAFE -> status LED off


def test_armed_moves_servo_with_tracks():
    cfg, loop, drv, servo, events = _loop()      # default SEARCHING == armed
    loop.tick([make_track(track_id=1, cx=0, cy=0)])
    assert drv.writes != []


def test_disarmed_freezes_servo_but_still_reports_aim():
    cfg, loop, drv, servo, events = _loop()
    target = make_track(track_id=1, cx=0, cy=0)
    loop.sm.enter_safe()                          # DISARM
    drv.writes.clear()
    tel = loop.tick([target])
    assert drv.writes == []                       # frozen: no servo motion
    assert tel.selected_target_id == 1            # but still tracks + reports
    assert tel.predicted_xy is not None           # where it *would* aim


class _FakePump:
    def __init__(self):
        self.fires = []
        self.offs = 0

    def fire(self, dur):
        self.fires.append(dur)

    def off(self):
        self.offs += 1


def test_manual_fire_pulses_pump_in_any_state():
    cfg = Config()
    cfg.fire.fire_duration_s = 0.8
    drv = FakeDriver()
    servo = ServoController(drv, cfg.servo)
    sm = FireStateMachine(cfg.fire)
    pump = _FakePump()
    loop = ControlLoop(cfg, servo, TargetSelector(min_target_dwell_frames=1), sm, pump=pump)
    sm.enter_safe()                                  # disarmed
    assert loop.manual_fire() is True
    assert pump.fires == [0.8]                        # fires even while SAFE (manual override)
    assert loop.manual_pump_off() is True and pump.offs == 1


def test_manual_fire_without_pump_is_false():
    cfg = Config()
    servo = ServoController(FakeDriver(), cfg.servo)
    loop = ControlLoop(cfg, servo, TargetSelector(min_target_dwell_frames=1),
                       FireStateMachine(cfg.fire))
    assert loop.manual_fire() is False


def test_manual_marker_forces_aux_independent_of_state():
    cfg = Config()
    cfg.fire.enabled = False
    drv = FakeDriver()
    servo = ServoController(drv, cfg.servo)
    sm = FireStateMachine(cfg.fire)
    aux = _FakeIndicator()
    loop = ControlLoop(cfg, servo, TargetSelector(min_target_dwell_frames=1), sm,
                       aux_marker=aux)
    loop.tick([])
    assert aux.state is False                     # opt-in marker: off by default
    assert loop.set_marker(True) is True
    assert aux.state is True                       # manual force -> on now
    sm.enter_safe()
    loop.tick([])
    assert aux.state is True                        # stays on while disarmed (boresight)
    loop.set_marker(False)
    assert aux.state is False                       # back to auto (off, not aiming)

