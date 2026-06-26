"""Pump actuation — non-blocking, OFF on every exit path.

The pump is driven through a relay/MOSFET (with a flyback diode) via a gpiozero
``OutputDevice``, never a bare GPIO. ``fire`` turns the pump on and schedules an
OFF on a timer — it never ``time.sleep``s. Every error path turns the pump OFF.

The injected ``device`` and ``timer_factory`` let the Mac test on/off ordering and
the non-blocking behaviour without hardware. The control-loop state machine drives
``on``/``off`` with its own clock; ``fire`` is the standalone self-timed path.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from errors import PumpError


class Pump:
    def __init__(self, device: Optional[Any] = None, gpio_bcm: int = 26,
                 active_high: bool = True,
                 timer_factory: Callable[..., Any] = threading.Timer):
        if device is None:
            from gpiozero import OutputDevice  # lazy: hardware-only
            device = OutputDevice(gpio_bcm, active_high=active_high,
                                  initial_value=False)
        self._device = device
        self._timer_factory = timer_factory
        self._timer: Optional[Any] = None
        self._is_on = False

    @property
    def is_on(self) -> bool:
        return self._is_on

    def on(self) -> None:
        try:
            self._device.on()
            self._is_on = True
        except Exception as exc:  # noqa: BLE001
            self._safe_off()
            raise PumpError("pump on failed") from exc

    def off(self) -> None:
        self._cancel_timer()
        self._safe_off()

    def fire(self, duration_s: float) -> None:
        """Pump on, then auto-off after ``duration_s`` (non-blocking)."""
        try:
            self.on()
            self._cancel_timer()
            self._timer = self._timer_factory(duration_s, self.off)
            self._timer.daemon = True
            self._timer.start()
        except PumpError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.off()
            raise PumpError("pump fire failed") from exc

    def close(self) -> None:
        self.off()
        closer = getattr(self._device, "close", None)
        if callable(closer):
            closer()

    def _safe_off(self) -> None:
        try:
            self._device.off()
        finally:
            self._is_on = False

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            cancel = getattr(self._timer, "cancel", None)
            if callable(cancel):
                cancel()
            self._timer = None
