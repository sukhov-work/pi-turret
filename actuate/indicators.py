"""GPIO indicator outputs — status LED (BCM23) and aux marker (BCM27).

Same pins and same driver as v1 (``gpiozero.LED``) — no rewiring:
  - **status LED, BCM23**: lit while ARMED / actively scanning, off when SAFE.
  - **aux marker, BCM27**: v1's aux laser; OPT-IN aim marker, off by default
    (laser safety — never auto-driven unless ``app.aux_marker_enabled``).

Fail-safe like the LCD: an indicator error logs and is swallowed, never crashing
the control loop. ``enabled=False`` makes every call a no-op.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GpioOutput:
    def __init__(self, gpio_bcm: int, enabled: bool = True,
                 device: Optional[Any] = None):
        self._enabled = enabled
        self._dev = device
        self._state = False
        if enabled and device is None:
            try:
                from gpiozero import LED  # lazy: Pi-only. v1 used LED() for these pins.
                self._dev = LED(gpio_bcm)
            except Exception:  # noqa: BLE001
                logger.warning("GPIO output init failed (BCM%s)", gpio_bcm, exc_info=True)
                self._dev = None

    @property
    def is_on(self) -> bool:
        return self._state

    def on(self) -> None:
        self._drive(True)

    def off(self) -> None:
        self._drive(False)

    def set(self, value: bool) -> None:
        self._drive(bool(value))

    def close(self) -> None:
        self.off()
        if self._dev is not None and hasattr(self._dev, "close"):
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                logger.warning("GPIO output close failed", exc_info=True)

    def _drive(self, value: bool) -> None:
        if not self._enabled or self._dev is None:
            self._state = value
            return
        try:
            self._dev.on() if value else self._dev.off()
        except Exception:  # noqa: BLE001
            logger.warning("GPIO output write failed", exc_info=True)
        finally:
            self._state = value
