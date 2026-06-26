"""Pump: non-blocking fire, OFF on every exit path."""
import pytest

from actuate.pump import Pump
from errors import PumpError


class FakeDevice:
    def __init__(self, raise_on_on=False):
        self.events = []
        self.closed = False
        self._raise = raise_on_on

    def on(self):
        if self._raise:
            raise OSError("gpio boom")
        self.events.append("on")

    def off(self):
        self.events.append("off")

    def close(self):
        self.closed = True


class FakeTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.started = False
        self.cancelled = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def _timer_factory(created):
    def make(interval, callback):
        t = FakeTimer(interval, callback)
        created.append(t)
        return t
    return make


def test_on_off_transitions():
    dev = FakeDevice()
    pump = Pump(device=dev)
    pump.on()
    assert pump.is_on and dev.events == ["on"]
    pump.off()
    assert not pump.is_on and dev.events == ["on", "off"]


def test_fire_is_non_blocking_and_schedules_off():
    dev = FakeDevice()
    created = []
    pump = Pump(device=dev, timer_factory=_timer_factory(created))
    pump.fire(1.0)
    # returns immediately; pump on; an off-timer is armed for the duration
    assert pump.is_on and dev.events == ["on"]
    assert len(created) == 1
    assert created[0].interval == 1.0 and created[0].started
    # when the timer fires, the pump turns off
    created[0].callback()
    assert not pump.is_on and dev.events == ["on", "off"]


def test_on_failure_turns_off_and_raises():
    dev = FakeDevice(raise_on_on=True)
    pump = Pump(device=dev)
    with pytest.raises(PumpError):
        pump.on()
    assert dev.events == ["off"]  # OFF on the error path


def test_fire_failure_turns_off_and_raises():
    dev = FakeDevice(raise_on_on=True)
    created = []
    pump = Pump(device=dev, timer_factory=_timer_factory(created))
    with pytest.raises(PumpError):
        pump.fire(1.0)
    assert "off" in dev.events
    assert created == []  # never armed a timer


def test_close_turns_off_and_closes_device():
    dev = FakeDevice()
    pump = Pump(device=dev)
    pump.on()
    pump.close()
    assert not pump.is_on and dev.closed and dev.events[-1] == "off"
