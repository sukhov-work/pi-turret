"""LCD rendering — what to show across the lifecycle (view layer, in app/).

``format_lcd_lines`` is pure and unit-tested; ``LcdReporter`` is a low-rate thread
(Pi-side) that renders the latest telemetry so the control loop is never blocked by
I2C. Lines are 16 chars (1602A). v1 only showed on/off + angles; v2 surfaces state,
selected target, aim error, kill-zone, fps, and shot count throughout the run.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, Tuple

from actuate.lcd import LCD_WIDTH, StatusLcd
from app.statemachine import FireState

logger = logging.getLogger(__name__)

_SPINNER = "|/-\\"


def _fit(text: str) -> str:
    return text[:LCD_WIDTH]


def format_lcd_lines(state: FireState, telemetry=None, *, armed: bool = False,
                     fps: Optional[float] = None, shots: int = 0,
                     spinner_idx: int = 0) -> Tuple[str, str]:
    """Render two 16-char LCD lines for the current state + telemetry."""
    arm = "ARM" if armed else "SAFE"
    spin = _SPINNER[spinner_idx % len(_SPINNER)]
    fps_str = f"{fps:4.1f}" if fps is not None else " -- "

    if telemetry is None:
        return _fit("pi-turret v2"), _fit("starting...")

    if state is FireState.SAFE:
        return _fit("** SAFE **"), _fit("disarmed")

    if state is FireState.FIRING:
        tid = telemetry.selected_target_id
        return _fit(f"FIRE! #{tid}"), _fit(f"shots:{shots}")

    if state is FireState.COOLDOWN:
        return _fit("COOLDOWN"), _fit(f"shots:{shots} {arm}")

    if state in (FireState.TRACKING, FireState.AIMING):
        tid = telemetry.selected_target_id
        err = telemetry.aim_error_px
        kz = "Y" if telemetry.in_killzone else "N"
        wf = "WF" if telemetry.would_fire else "  "
        return _fit(f"AIM#{tid} e{err:4.0f}"), _fit(f"KZ:{kz} {wf} {arm}")

    # SEARCHING (and any fallback)
    return _fit(f"SCAN {spin} {fps_str}f"), _fit(f"trk:{telemetry.num_tracks} {arm}")


class LcdReporter:
    """Renders latest telemetry to the LCD at a fixed low rate (Pi-side thread)."""

    def __init__(self, lcd: StatusLcd, telemetry_slot, refresh_hz: float = 4.0,
                 armed_getter: Optional[Callable[[], bool]] = None,
                 fps_getter: Optional[Callable[[], float]] = None,
                 shots_getter: Optional[Callable[[], int]] = None):
        self._lcd = lcd
        self._slot = telemetry_slot
        self._period_s = 1.0 / max(0.5, refresh_hz)
        self._armed = armed_getter or (lambda: False)
        self._fps = fps_getter or (lambda: None)
        self._shots = shots_getter or (lambda: 0)
        self._running = False
        self._spin = 0
        self._thread: Optional[threading.Thread] = None

    def message(self, line1: str, line2: str = "") -> None:
        """Push a one-off lifecycle message (boot, IP, disarm)."""
        self._lcd.show(line1, line2)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="lcd", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            tel = self._slot.get()
            state = tel.state if tel is not None else FireState.SEARCHING
            try:
                l1, l2 = format_lcd_lines(state, tel, armed=self._armed(),
                                          fps=self._fps(), shots=self._shots(),
                                          spinner_idx=self._spin)
                self._lcd.show(l1, l2)
            except Exception:  # noqa: BLE001 — display must never crash the app
                logger.warning("LCD render failed", exc_info=True)
            self._spin += 1
            threading.Event().wait(self._period_s)
