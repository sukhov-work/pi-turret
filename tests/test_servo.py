"""ServoController: clamps (angle + pulse), mapping, channels, failsafe."""
from dataclasses import replace

import pytest

from config import ServoConfig
from actuate.servo import Axis, ServoController
from errors import ServoError

CFG = ServoConfig()


class FakeDriver:
    def __init__(self, raise_on_write=False):
        self.writes = []          # (channel, pulse_us)
        self.relaxed = []
        self._raise = raise_on_write

    def set_servo_pulse(self, channel, pulse_us):
        if self._raise:
            raise OSError("i2c boom")
        self.writes.append((channel, pulse_us))

    def relax(self, channel):
        self.relaxed.append(channel)


def test_angle_to_pulse_matches_v1_formula():
    sc = ServoController(FakeDriver(), CFG)
    # v1: pulse_us = deg * (2000/180) + 501
    assert sc.angle_to_pulse_us(31) == pytest.approx(31 * (2000 / 180) + 501)


def test_set_angle_in_range_writes_expected_pulse():
    drv = FakeDriver()
    sc = ServoController(drv, CFG)
    used = sc.set_angle(Axis.PAN, 31.0)
    assert used == 31.0
    ch, us = drv.writes[-1]
    assert ch == CFG.pan_channel
    assert us == pytest.approx(31 * (2000 / 180) + 501)


def test_angle_clamped_before_write():
    drv = FakeDriver()
    sc = ServoController(drv, CFG)
    assert sc.set_angle(Axis.PAN, 90.0) == CFG.pan_max_deg   # 47
    assert sc.set_angle(Axis.PAN, -5.0) == CFG.pan_min_deg   # 5
    assert sc.set_angle(Axis.TILT, 90.0) == CFG.tilt_max_deg  # 25


def test_pulse_clamped_to_guard():
    drv = FakeDriver()
    cfg = replace(CFG, pulse_max_us=600.0)  # force the pulse guard to bite
    sc = ServoController(drv, cfg)
    sc.set_angle(Axis.PAN, 47.0)            # maps to ~1023us, clamped to 600
    assert drv.writes[-1][1] == 600.0


def test_channel_mapping():
    drv = FakeDriver()
    sc = ServoController(drv, CFG)
    sc.set_angle(Axis.PAN, 20.0)
    sc.set_angle(Axis.TILT, 20.0)
    assert drv.writes[0][0] == CFG.pan_channel   # 1
    assert drv.writes[1][0] == CFG.tilt_channel  # 0


def test_driver_failure_raises_servo_error():
    sc = ServoController(FakeDriver(raise_on_write=True), CFG)
    with pytest.raises(ServoError):
        sc.set_angle(Axis.PAN, 20.0)


def test_center_writes_home_angles():
    drv = FakeDriver()
    sc = ServoController(drv, CFG)
    sc.center()
    assert sc.last_angle(Axis.PAN) == CFG.home_pan_deg
    assert sc.last_angle(Axis.TILT) == CFG.home_tilt_deg


def test_disarm_relaxes_both_servos():
    drv = FakeDriver()
    sc = ServoController(drv, CFG)
    sc.disarm()
    assert set(drv.relaxed) == {CFG.pan_channel, CFG.tilt_channel}
