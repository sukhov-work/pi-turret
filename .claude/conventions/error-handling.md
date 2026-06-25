# Error Handling

This controls a machine that physically moves and fires. The guiding rule: **on any
error in an actuation path, fail to a safe state — never leave the pump on or the servos
driving.** Pure-logic layers (decode, aim math, strategy) should let bugs raise so tests
catch them.

## Exception Hierarchy

```python
class TurretError(Exception):
    """Base for all pi-turret errors."""

class ConfigError(TurretError):
    """Invalid/missing tunable or calibration."""

class CameraError(TurretError):
    """picamera2 init or capture failed."""

class DetectionError(TurretError):
    """Model load or inference failed."""

class ServoError(TurretError):
    """PCA9685 / I2C write failed — treat as safety-critical."""

class PumpError(TurretError):
    """Relay/pump actuation failed — safety-critical."""
```

## Error Strategy by Layer

| Layer | Strategy | Rationale |
|-------|----------|-----------|
| Camera capture | Catch, log warning, skip the frame, keep the loop alive | One bad frame must not stop tracking |
| Detection / decode | Catch per-frame, return no detections, continue | A model hiccup ≠ crash |
| Tracking / strategy / aim | Let exceptions propagate (pure logic) | Bugs here are test failures, not runtime |
| Servo (actuate) | Catch `ServoError` → **disarm** (relax + center), surface to state machine | A servo that won't obey must not keep being commanded |
| Pump (actuate) | `try/finally` → **pump OFF on every exit path** | Never leave water/laser running |
| Control loop | Catch-all at the top → log `exception`, enter **SAFE** state, keep process alive | A dead loop with armed hardware is the worst outcome |
| Bottle handlers | Catch-all → return 500, never crash the server thread | UI errors must not take down control |
| Startup | Fail fast with a clear `TurretError` if hardware/model missing | Better to refuse to arm than half-init |

## Patterns

### Failsafe actuation (the most important pattern)

```python
def fire(self, duration_s: float) -> None:
    try:
        self.pump.on()
        self._fire_timer.start(duration_s, self.pump.off)   # non-blocking
    except Exception:
        logger.exception("fire failed")
        self.pump.off()        # OFF on the error path too
        raise PumpError from None
    # NOTE: never `time.sleep(duration_s)` here — that blocks the loop
```

### Disarm on servo failure

```python
try:
    self.servo.set_angle(axis, clamp(angle_deg))
except ServoError:
    logger.exception("servo write failed -> disarming")
    self.disarm()          # relax servos, pump off, set SAFE state
```

### Per-frame isolation in the detection loop

```python
while self.running:
    try:
        frame = self.camera.read_frame()
        detections = self.detector.infer(frame)
        self._process(detections)
    except CameraError:
        logger.warning("frame skipped")
        continue
    except Exception:
        logger.exception("loop iteration failed -> SAFE")
        self.enter_safe_state()
```

### Graceful exit (keep v1's instinct, fix its bareness)

Register `atexit` + `SIGTERM`/`SIGINT` handlers that disarm: center & relax servos, pump
off, LEDs off, release camera. v1 did this in `gracefulExit` — keep it, but catch
specific exceptions inside, not a bare `except:`.

## Logging

`logger = logging.getLogger(__name__)` per module. (`syslog` is fine on the Pi for the
service, but go through `logging`.)

- `DEBUG` — per-frame internals (fps, detection counts) when diagnosing
- `INFO` — state transitions (SEARCHING→TRACKING→FIRING→COOLDOWN), arm/disarm
- `WARNING` — recoverable (frame skipped, target lost, calibration drift)
- `ERROR` via `logger.exception(...)` — any failure with a traceback; **always** before a disarm

Include the input that failed (axis + angle, frame index, model path).

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Bare `except:` (v1 had several) | Catch specific exceptions; `Exception` only at loop/handler top |
| Leave pump/laser on after an exception | `try/finally` → OFF on every path |
| Swallow a `ServoError` and keep aiming | Disarm, then surface it |
| `time.sleep()` retry loop on the hot path | Non-blocking timer + state machine |
| Raise in `__init__` leaving hardware half-open | Validate first; clean up on failure |
| Generic message ("error") | Name the axis/frame/path that failed |
| Crash the Bottle thread on a bad request | Catch-all → 500 |
