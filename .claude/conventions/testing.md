# Testing Standards

Framework: **pytest** (synchronous — no async). The defining constraint is **where a test
can run**: most logic is pure and runs on the **Mac**; anything touching the camera, Coral,
servos, pump, or real timing is **Pi-only truth** and cannot be asserted off-device.

## The test split (read this first)

| Tier | Runs on | What | Gate |
|------|---------|------|------|
| **Pure-logic unit** | Mac (fast `pytest`) | decode, NMS, calibration transform, controller step, state machine, lead/parallax, clamping | the bulk of coverage; CI-able |
| **Hardware-mock integration** | Mac | a layer wired to fake camera / stub detector / mock servo bus / fake pump | asserts orchestration + that clamps/failsafe are honored |
| **On-device** | Pi only | import has no side effects; real `model.predict`; measured FPS/latency; servo dry-run within clamps; decoy fire | manual + recorded numbers; never faked |

"Runs on the Mac" ≠ "verified" for hardware. Don't claim an FPS/accuracy/aiming result that
wasn't measured on the Pi.

## Directory Structure

**Flat `tests/` — one `test_{file}.py` per module** (mirrors the flat repo-root package layout;
`pytest.ini` sets `pythonpath = .`). As-built:

```
tests/
    conftest.py             # shared fixtures + Detection/Track factories (mock hardware, not logic)
    fixtures/               # generate_golden_fixture.py + raw_output.npy + predict_ref.json
    test_decode.py          # the golden v8-decode test (see below)
    test_nms.py
    test_calibration.py  test_controller.py  test_killzone.py
    test_scoring.py      test_selector.py    test_tracker.py    test_predict.py
    test_fire_state_machine.py  test_control_loop.py  test_servo.py  test_pump.py
    test_lcd.py  test_remote.py  test_streamer.py  test_web.py  test_snapshots.py
    test_config.py  test_imports.py  test_latest_slot.py
```

## The golden test: decode vs reference

The one test that must never regress — it guards against re-introducing the v5/v8 decoder
bug that wrecked v1's Coral accuracy. It is **live as of run1** (`tests/test_decode.py::
test_v8_decode_matches_ultralytics_reference`):

```python
def test_v8_decode_matches_ultralytics_reference():
    raw = np.load("tests/fixtures/raw_output.npy")          # dequantized model output [1, 5, N]
    ref = json.load(open("tests/fixtures/predict_ref.json"))  # boxes from Ultralytics predict
    dets = decode_v8(raw, ref["input_size_px"], ref["frame_width_px"], ref["frame_height_px"],
                     conf_threshold=ref["conf"], iou_threshold=ref["iou"],
                     coords_normalized=ref["coords_normalized"])
    # match count + class + xyxy within abs=2px
```

Facts pinned by the run1 fixture (verified on Strix + Pi, 2026-06-29):
- **Anchor count varies with input size:** `[1,5,1344]` at 256 (`1344 = 32²+16²+8²`), **not 8400**
  (that's 640). `decode_v8` is anchor-count-agnostic.
- **`coords_normalized=True`** — Ultralytics v8 *detection* tflite emits xywh in `[0,1]`; the `False`
  path mis-decodes by ~1100 px. (The `.pt` emits input-pixels — don't capture the fixture from it.)
- **`decode_v8` clips boxes to frame bounds** (matches Ultralytics `clip_boxes`).

**Regenerate the fixture per model** with `tests/fixtures/generate_golden_fixture.py` (runs on Strix
against the run's `_full_integer_quant.tflite`; it self-pins `coords_normalized`). Full procedure:
`claude-docs/MODEL_ITERATION.md`.

## Fixtures: mock the hardware, not the logic

```python
@pytest.fixture
def fake_camera():
    cam = MagicMock()
    cam.read_frame.return_value = np.load("tests/fixtures/frame.npy")
    return cam

@pytest.fixture
def stub_detector():
    det = MagicMock(spec=Detector)
    det.infer.return_value = [Detection(box=(100, 100, 40, 40), score=0.9, cls=0)]
    return det

@pytest.fixture
def mock_servo_bus():
    """Records writes so tests can assert clamping + ordering."""
    bus = MagicMock(spec=PCA9685)
    bus.writes = []
    bus.set_angle.side_effect = lambda ch, a: bus.writes.append((ch, a))
    return bus

@pytest.fixture
def fake_pump():
    pump = MagicMock()
    pump.state = []
    pump.on.side_effect = lambda: pump.state.append("on")
    pump.off.side_effect = lambda: pump.state.append("off")
    return pump
```

## Test Marks

```python
@pytest.mark.hardware     # needs a real Pi / I2C / camera — skipped on the Mac
@pytest.mark.slow         # loads a model or large fixture
@pytest.mark.integration  # multiple layers wired with fakes
```

Run fast Mac tests: `python -m pytest -m "not hardware and not slow" -v`

## What to Test

| Component | Must test | Skip (Pi-only or N/A) |
|-----------|-----------|------------------------|
| v8 decode | transpose, no-objectness, xywh×input, class index, threshold | real inference latency |
| NMS / IoU | overlap suppression, score ordering, empty input | |
| Calibration | pixel→angle transform, inverse, out-of-frame handling | true on-device aim error |
| Controller | P/PI step, deadband, output clamp, no windup | servo settling time |
| Fire state machine | SEARCHING→TRACKING→FIRING→COOLDOWN, cooldown gate, **pump OFF on every transition out of FIRING** | real pump timing |
| Human interlock | person frame → **no fire**, conservative threshold | |
| Servo clamp | angle/pulse clamped to limits before write; rejects out-of-range | physical travel |
| Lead / parallax | predicted impact point math | wet accuracy |

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Assert FPS / accuracy from the Mac | Measure on the Pi; mark `@pytest.mark.hardware` |
| Need real hardware for logic tests | Inject fake camera/detector/servo/pump |
| Test private internals | Test behavior: inputs → boxes / angles / state |
| Skip the decode-vs-reference test | Keep it green — it's the v5/v8 guardrail |
| Hand-build detection dicts in every test | Use `Detection`/`Track` factory helpers in `conftest.py` |
| Leave `@pytest.mark.hardware` tests in the fast run | Gate them out: `-m "not hardware"` |
| Report success with failing tests | Fix failures first |
