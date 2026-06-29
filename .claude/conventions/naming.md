# Naming Conventions

Python (PEP 8): `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE`
constants. Below are the project-specific patterns for the v2 tree.

## Package Structure

```
<v2-root>/
    {layer}/                # detect, track, strategy, aim, actuate, app
        __init__.py         # re-exports the public interface of the layer
        {backend}.py        # one file per interchangeable backend (e.g. detect/coral.py)
    config.py               # tunables singleton
    models.py / contracts   # shared dataclasses (Detection, Track)
```

## Files

| Type | Pattern | Example |
|------|---------|---------|
| Layer entry | `{layer}/__init__.py` | `detect/__init__.py` |
| Backend impl | `{layer}/{backend}.py` | `detect/coral.py`, `detect/cpu.py` |
| Hardware driver | `actuate/{device}.py` | `actuate/servo.py`, `actuate/pump.py` |
| Shared contracts | `contracts.py` or `models.py` | `Detection`, `Track` dataclasses |
| Config | `config.py` | tunables in one place |
| Entrypoint | `app/main.py` | Bottle + thread orchestration |
| Tests | flat `tests/test_{file}.py` (one per module) | `tests/test_decode.py`, `tests/test_servo.py` |

## Classes

| Type | Pattern | Example |
|------|---------|---------|
| Hardware controller | `{Device}Controller` / `{Device}` | `ServoController`, `PCA9685` |
| Backend impl | `{Backend}{Role}` | `CoralDetector`, `CpuDetector` |
| Tracker | `{Algo}Tracker` | `ByteTracker` |
| Controller (control theory) | `{Kind}Controller` | `PIController` |
| State machine | `{Domain}StateMachine` / `{Domain}State` (Enum) | `FireStateMachine`, `FireState` |
| Data contract | `{Entity}` (dataclass) | `Detection`, `Track`, `Calibration` |
| Enum | `{Domain}{Purpose}` | `FireState`, `Axis`, `Backend` |
| Exception | `{Domain}Error` | `ServoError`, `CameraError`, `DetectionError` |

## Functions

| Type | Pattern | Example |
|------|---------|---------|
| Hardware action | `verb_noun` | `set_angle`, `read_frame`, `fire_pump`, `disarm` |
| Computation | `compute_*` | `compute_pixel_to_angle`, `compute_lead` |
| Decode / transform | `decode_*`, `to_*` | `decode_v8`, `to_xyxy` |
| Boolean | `is_*`, `has_*`, `should_*` | `is_in_kill_zone`, `should_fire` |
| Lifecycle | `start`, `stop`, `step`, `close` | `controller.step(error)` |
| Factory | `get_{thing}` | `get_servo_bus`, `get_detector` |

## Variables

| Type | Pattern | Example |
|------|---------|---------|
| Hardware handles | `{device}` / `{device}_bus` | `camera`, `servo_bus`, `pump` |
| Angles / pulses | `*_deg`, `*_us`, `*_pulse` | `pan_deg`, `tilt_deg`, `pulse_us` |
| Pixel coords | `*_px`, `cx`, `cy` | `error_px`, `cx`, `cy` |
| Config values | from `config.*` | `config.PAN_MIN_DEG`, `config.CONF_THRESHOLD` |
| Constants | `UPPER_SNAKE` | `PAN_MIN_DEG`, `PULSE_MIN_US`, `INPUT_SIZE` |
| Private attrs | `_prefix` | `self._lock`, `self._model` |

**Units belong in the name.** Angles are `_deg`, pulse widths `_us`, pixels `_px`. v1's
bare `HPulse`/`angle_x` forced readers to guess units — don't.

## Avoid copying v1 identifiers

v1 carries typos that must not propagate into v2: `detection_confidende_*`,
`detetion_results`, and `rotateGraduallyByPulse` (which doesn't actually move gradually).
Name v2 symbols for what they do.

## Imports

Order: stdlib → third-party → project. One import per line; let the formatter sort.

```python
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from detect import Detection
from config import PAN_MIN_DEG
```

Do not duplicate imports (v1 imported `RPi.GPIO` twice) and do not import libraries you
don't use (v1 imported `ultralytics.YOLO` but inferred via `onnxruntime`).
