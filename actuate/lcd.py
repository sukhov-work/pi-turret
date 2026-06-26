"""1602A LCD device (rpi_lcd) — fail-safe; the display must never crash control.

Same hardware as v1: a 16x2 I2C LCD on **bus 1** (rpi_lcd default address), sharing
the bus with the PCA9685 @ 0x40 — no rewiring. ``rpi_lcd.LCD.text(msg, row)`` uses
1-indexed rows (1 = top, 2 = bottom). Every method swallows hardware errors and
logs: a flaky LCD degrades to "no display", it does not stop the turret.

The *rendering* (what to show per lifecycle phase) lives in ``app/display.py``;
this module is the dumb device.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

LCD_WIDTH = 16


class StatusLcd:
    def __init__(self, enabled: bool = True, device: Optional[Any] = None):
        self._enabled = enabled
        self._lcd = device
        if enabled and device is None:
            try:
                from rpi_lcd import LCD  # lazy: Pi-only (I2C)
                self._lcd = LCD()
            except Exception:  # noqa: BLE001 — degrade to no-display, never crash
                logger.warning("LCD init failed; running without display", exc_info=True)
                self._lcd = None

    @property
    def active(self) -> bool:
        return self._enabled and self._lcd is not None

    def show(self, line1: str = "", line2: str = "") -> None:
        if not self.active:
            return
        try:
            self._lcd.text(line1[:LCD_WIDTH], 1)
            self._lcd.text(line2[:LCD_WIDTH], 2)
        except Exception:  # noqa: BLE001
            logger.warning("LCD write failed", exc_info=True)

    def clear(self) -> None:
        if not self.active:
            return
        try:
            self._lcd.clear()
        except Exception:  # noqa: BLE001
            logger.warning("LCD clear failed", exc_info=True)

    def close(self) -> None:
        self.clear()
