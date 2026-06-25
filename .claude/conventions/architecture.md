# Architecture & Concurrency Patterns

pi-turret is a **real-time, threaded, synchronous** Python app on a Raspberry Pi — *not*
an async service. The dominant concern is a control loop that must keep running while the
hardware (camera, servos, pump) is actuated. Optimize for predictable latency and
fail-safe actuation, not throughput.

> v1 is the working rollback and is **never edited in place**. Everything below is the
> **v2** target. Build v2 in its own tree + venv (layout per `IMPLEMENTATION_PLAN.md`).

## Core Pattern: Threaded Pipeline with Single-Slot Buffers

Capture, inference, and control run on separate threads. Threads communicate through
**lock-protected, latest-wins single-slot buffers** — never unbounded queues (a backlog
means you're aiming at where the bird *was*).

```python
class LatestSlot:
    """Latest-value-wins buffer. Drop stale frames; aim at the present."""
    def __init__(self):
        self._lock = threading.Lock()
        self._value = None

    def put(self, value):
        with self._lock:
            self._value = value

    def get(self):
        with self._lock:
            return self._value
```

**Rules:**
- Every cross-thread read/write goes through a lock. No bare `global` shared between threads (v1's biggest footgun).
- The control loop **never blocks**: no `time.sleep` on the hot path, no synchronous fire.
- One owner per actuator. Manual jog and auto-track must not drive the servos at once — gate by state, hold a lock for the duration of a move.

## Module Organization (v2 tree)

```
<v2-root>/
    capture.py      # camera abstraction: Pi Camera (detection) + USB (stream)
    detect/         # model load + inference + correct anchor-free v8 decode + NMS
    track/          # multi-object tracking (stable IDs) + constant-velocity lead prediction
    strategy/       # scoring + target selection/switching (tunable)
    aim/            # pixel -> angle calibration + P/PI controller + kill-zone geometry
    actuate/        # servo (PCA9685, init-once) + pump via relay/MOSFET, failsafe
    app/            # state machine, thread pipeline, annotation, snapshots, Bottle web
    config.py       # all tunables (clamps, thresholds, calibration)
    main.py         # entrypoint, guarded by `if __name__ == '__main__'`
```

Keep the **data path one-directional**: `detect -> track -> strategy -> aim -> actuate`.
`app/` wires threads and owns lifecycle; it does not contain control logic.

## Backend Abstraction (the compile-gate decision)

The detector backend is chosen by a build gate (Coral INT8 primary, CPU fallback per
`IMPLEMENTATION_PLAN.md` D1). Hide that choice behind one interface so `strategy`/`aim`
never know which backend ran.

```python
class Detector(Protocol):              # 3.9: use typing.Protocol or an ABC
    def infer(self, frame) -> "list[Detection]": ...
```

Backends (`CoralDetector`, `CpuDetector`) implement it; selection happens once at startup.
`Detection` / `Track` are plain dataclasses — the stable contract between layers.

## Singletons: init expensive hardware once

The camera handle, the PCA9685 bus, and the loaded model are **one per process**.
Construct them once in `app/` startup and inject them; never re-open per frame.

```python
@lru_cache(maxsize=1)
def get_servo_bus() -> PCA9685: ...
```

PCA9685 is initialized **once** (MODE2 set at start) — do **not** toggle MODE2 per move
(v1 did; it adds latency/jitter).

## Configuration: one source of truth

Every tunable lives in `config.py` (or a calibration JSON it loads) — pan/tilt clamps,
pulse band, confidence/IoU thresholds, calibration coefficients, cooldown timings. **No
magic numbers inline.** v1 scattered `576`, `25`, `0.7`, `1040`, `±5` across files; v2
names them once.

## Importability: no side effects at import

Modules must import cleanly on the **Mac with no hardware present**. All hardware init and
run loops go behind `if __name__ == '__main__':` or explicit `start()` functions.

> `v1/TurretHandler.py` ran a full hardware init + infinite detection loop *at import*
> (trailing unguarded module code). Never reproduce that — it breaks every unit test.

## The web layer (Bottle)

Bottle is a synchronous WSGI server on its own thread. Route handlers must be thin: read
state / post a command to a slot, return immediately. **No detection or servo work inside
a request handler** — it would block the server thread.

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Hardware init / run loop at module import | Guard with `if __name__ == '__main__':` or a `start()` fn |
| Share state between threads via bare `global` | Lock-protected single-slot buffers |
| `time.sleep` in the control/fire path | Non-blocking timer + state machine |
| Unbounded frame queue | Latest-wins single slot (drop stale frames) |
| Re-open camera/model/bus per frame | Init once, inject the singleton |
| Magic numbers inline | Name them in `config.py` |
| Detection/servo logic in a Bottle route | Route flips a flag; worker thread acts |
| Two code paths driving one servo | One owner; gate by state under a lock |
| `import cv2` side effects / `imshow` in headless service | Pure modules; headless by default |
| Mix v1 and v2 code | v2 in its own tree; v1 stays the rollback |
