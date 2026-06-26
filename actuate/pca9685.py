"""PCA9685 16-channel PWM servo driver (ported from v1, init-once).

Differences from v1's driver:
  - ``smbus`` is imported lazily in ``__init__`` so this module imports on the Mac.
  - MODE2 is set **once** in ``setup()``; we do NOT toggle it per move (v1 did,
    adding latency/jitter).
  - ``set_pwm`` keeps v1's ``int()`` coercion (the float/`&` TypeError is already
    fixed — do not "re-fix" it).

Hardware truth (I2C, real pulses, servo travel) is Pi-only.
"""
from __future__ import annotations


class PCA9685:
    _MODE1 = 0x00
    _MODE2 = 0x01
    _PRESCALE = 0xFE
    _LED0_ON_L = 0x06
    _LED0_ON_H = 0x07
    _LED0_OFF_L = 0x08
    _LED0_OFF_H = 0x09

    def __init__(self, address: int = 0x40, busnum: int = 1, debug: bool = False):
        import smbus  # lazy: hardware-only, absent on the Mac
        self._bus = smbus.SMBus(busnum)
        self.address = address
        self.debug = debug
        self.write(self._MODE1, 0x00)

    def write(self, reg: int, value: int) -> None:
        self._bus.write_byte_data(self.address, reg, value)

    def read(self, reg: int) -> int:
        return self._bus.read_byte_data(self.address, reg)

    def setup(self, freq_hz: int = 50) -> None:
        """Set PWM frequency and enable outputs ONCE at startup."""
        self.set_pwm_freq(freq_hz)
        self.write(self._MODE2, 0x04)

    def set_pwm_freq(self, freq_hz: int) -> None:
        import math
        prescaleval = 25000000.0 / 4096.0 / float(freq_hz) - 1.0
        prescale = int(math.floor(prescaleval + 0.5))
        oldmode = self.read(self._MODE1)
        self.write(self._MODE1, (oldmode & 0x7F) | 0x10)  # sleep
        self.write(self._PRESCALE, prescale)
        self.write(self._MODE1, oldmode)
        import time
        time.sleep(0.005)
        self.write(self._MODE1, oldmode | 0x80)           # restart

    def set_pwm(self, channel: int, on: int, off: int) -> None:
        # int() coercion preserved from v1 (do not remove).
        self.write(self._LED0_ON_L + 4 * channel, int(on) & 0xFF)
        self.write(self._LED0_ON_H + 4 * channel, int(on) >> 8)
        self.write(self._LED0_OFF_L + 4 * channel, int(off) & 0xFF)
        self.write(self._LED0_OFF_H + 4 * channel, int(off) >> 8)

    def set_servo_pulse(self, channel: int, pulse_us: float) -> None:
        """Drive a channel to a pulse width in microseconds (50 Hz / 20 ms / 12-bit)."""
        counts = int(pulse_us * 4096 / 20000)
        self.set_pwm(channel, 0, counts)

    def relax(self, channel: int) -> None:
        """Stop driving a channel (0 = full off)."""
        self.set_pwm(channel, 0, 0)
