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

Mirror the v2 layer layout:

```
tests/
    conftest.py             # shared fixtures (fake hardware, sample frame)
    fixtures/               # a saved frame + its model.predict reference output
    test_detect/
        test_decode.py      # the golden v8-decode test (see below)
        test_nms.py
    test_aim/
        test_calibration.py
        test_controller.py
    test_strategy/
        test_fire_state_machine.py
        test_interlock.py
    test_actuate/
        test_clamp.py
```

## The golden test: decode vs reference

The one test that must never regress — it guards against re-introducing the v5/v8 decoder
bug that wrecked v1's Coral accuracy:

```python
def test_v8_decode_matches_ultralytics_reference():
    raw = np.load("tests/fixtures/raw_output.npy")        # saved [1, 5, 8400] tensor
    expected = json.load(open("tests/fixtures/predict_ref.json"))  # boxes from model.predict
    dets = decode_v8(raw, conf=0.25, iou=0.5)
    assert_boxes_close(dets, expected, tol_px=2)
    # asserts: transposed, NO objectness multiply, xywh*input, correct class index
```

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
