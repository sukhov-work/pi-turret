"""LCD rendering (pure) + StatusLcd / GpioOutput fail-safe behavior."""
from app.control import Telemetry
from app.display import format_lcd_lines
from app.statemachine import FireState
from actuate.lcd import StatusLcd, LCD_WIDTH
from actuate.indicators import GpioOutput


def _tel(state, **kw):
    base = dict(state=state, num_tracks=1, selected_target_id=7, aim_error_px=12.0,
                predicted_xy=(576, 576), pan_cmd_deg=30.0, tilt_cmd_deg=20.0,
                in_killzone=True, would_fire=False)
    base.update(kw)
    return Telemetry(**base)


def test_none_telemetry_shows_boot():
    l1, l2 = format_lcd_lines(FireState.SEARCHING, None)
    assert l1 == "pi-turret v2"
    assert "start" in l2


def test_searching_shows_tracks_and_arm_state():
    l1, l2 = format_lcd_lines(FireState.SEARCHING, _tel(FireState.SEARCHING, num_tracks=3),
                              armed=True, fps=24.0)
    assert "SCAN" in l1
    assert "trk:3" in l2 and "ARM" in l2


def test_aiming_shows_target_error_and_killzone():
    l1, l2 = format_lcd_lines(FireState.AIMING, _tel(FireState.AIMING, would_fire=True),
                              armed=True)
    assert "#7" in l1
    assert "KZ:Y" in l2 and "WF" in l2 and "ARM" in l2


def test_firing_shows_shots():
    l1, l2 = format_lcd_lines(FireState.FIRING, _tel(FireState.FIRING), shots=4)
    assert "FIRE" in l1 and "#7" in l1
    assert "shots:4" in l2


def test_safe_state():
    l1, l2 = format_lcd_lines(FireState.SAFE, _tel(FireState.SAFE))
    assert "SAFE" in l1
    assert "disarm" in l2


def test_lines_never_exceed_width():
    for state in FireState:
        l1, l2 = format_lcd_lines(state, _tel(state, selected_target_id=123456,
                                              aim_error_px=99999.0, num_tracks=99),
                                  armed=True, fps=1234.5, shots=99999)
        assert len(l1) <= LCD_WIDTH
        assert len(l2) <= LCD_WIDTH


# ---- device fail-safe (no hardware) ----

class _FlakyLcd:
    def text(self, *_):
        raise OSError("i2c boom")

    def clear(self):
        raise OSError("i2c boom")


def test_status_lcd_swallows_hardware_errors():
    lcd = StatusLcd(enabled=True, device=_FlakyLcd())
    # must not raise despite the device throwing on every call
    lcd.show("a", "b")
    lcd.clear()
    lcd.close()


def test_status_lcd_disabled_is_noop():
    lcd = StatusLcd(enabled=False)
    assert not lcd.active
    lcd.show("a", "b")  # no error


class _FakeOutput:
    def __init__(self):
        self.events = []

    def on(self):
        self.events.append("on")

    def off(self):
        self.events.append("off")


def test_gpio_output_set_and_failsafe():
    dev = _FakeOutput()
    out = GpioOutput(23, device=dev)
    out.on()
    assert out.is_on and dev.events == ["on"]
    out.set(False)
    assert not out.is_on and dev.events == ["on", "off"]


def test_gpio_output_disabled_is_noop():
    out = GpioOutput(23, enabled=False)
    out.on()
    assert out.is_on is True  # tracks intended state even with no device
