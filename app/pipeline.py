"""Thread pipeline + single-slot buffers.

Threads share **lock-protected, latest-wins single-slot buffers** — never queues,
so a slow stage drops stale frames instead of aiming at where the bird *was*.
``LatestSlot`` is unit-tested on the Mac; the full ``Pipeline`` (real camera +
detector + servos) is Pi-only and started behind an explicit ``start()``.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Generic, Optional, TypeVar

from app.control import ControlLoop
from app.statemachine import FireState
from track.tracker import IouTracker

logger = logging.getLogger(__name__)

T = TypeVar("T")


class LatestSlot(Generic[T]):
    """Latest-value-wins buffer. Drop stale values; read the present."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: Optional[T] = None

    def put(self, value: T) -> None:
        with self._lock:
            self._value = value

    def get(self) -> Optional[T]:
        with self._lock:
            return self._value


class Pipeline:
    """Wires capture -> inference -> control threads. Started on the Pi only.

    The control thread is the **only** servo mover. Each loop isolates per-frame
    failures: a bad frame or a model hiccup skips the tick; an actuation error
    disarms via the control loop's state machine.
    """

    def __init__(self, capture, detector, control: ControlLoop, tracker: IouTracker,
                 tick_hz: float = 30.0, reporter=None):
        self.capture = capture
        self.detector = detector
        self.control = control
        self.tracker = tracker
        self.reporter = reporter            # optional LcdReporter (Pi-side)
        self._period_s = 1.0 / tick_hz
        self.latest_frame: "LatestSlot" = LatestSlot()
        self.latest_tracks: "LatestSlot" = LatestSlot()
        self.latest_telemetry: "LatestSlot" = LatestSlot()
        self.shots = 0                      # FIRING edges, surfaced on the LCD
        self._fps = 0.0
        self._prev_state = None
        self._running = False
        self._threads = []

    @property
    def fps(self) -> float:
        return self._fps

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.reporter is not None:
            self.reporter.start()
        self._threads = [
            threading.Thread(target=self._capture_loop, name="capture", daemon=True),
            threading.Thread(target=self._inference_loop, name="inference", daemon=True),
            threading.Thread(target=self._control_loop, name="control", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._running = False
        if self.reporter is not None:
            self.reporter.stop()

    def _capture_loop(self) -> None:
        while self._running:
            try:
                self.latest_frame.put(self.capture.read_frame())
            except Exception:
                logger.warning("frame capture skipped", exc_info=True)

    def _inference_loop(self) -> None:
        last = None
        while self._running:
            frame = self.latest_frame.get()
            if frame is None:
                continue
            try:
                detections = self.detector.infer(frame)
                self.latest_tracks.put(self.tracker.update(detections))
                now = time.monotonic()
                if last is not None:
                    dt = now - last
                    if dt > 0:
                        inst = 1.0 / dt
                        self._fps = inst if self._fps == 0 else 0.8 * self._fps + 0.2 * inst
                last = now
            except Exception:
                logger.warning("inference tick skipped", exc_info=True)

    def _control_loop(self) -> None:
        while self._running:
            tracks = self.latest_tracks.get() or []
            try:
                telemetry = self.control.tick(tracks)
                if telemetry.state is FireState.FIRING and self._prev_state is not FireState.FIRING:
                    self.shots += 1            # count on the rising edge into FIRING
                self._prev_state = telemetry.state
                self.latest_telemetry.put(telemetry)
            except Exception:
                logger.exception("control tick failed -> SAFE")
                self.control.sm.enter_safe()
            threading.Event().wait(self._period_s)  # non-blocking pacing
