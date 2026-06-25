# mem:suggested_commands

Cheat-sheet. Each command notes its machine (see `mem:project/dev_environment`). (Referred by `mem:core`.)

## Run v1 (rollback reference) — on the Pi
```
cd v1 && python3 main.py        # Bottle UI on :8001 (v1's paths are relative to v1/)
```

## Tests (pure logic) — on the Mac
```
python -m pytest tests/ -v
python -m pytest -m "not hardware and not slow" -v    # fast subset
```

## Deploy v2 to the Pi
```
rsync -av --exclude .git ./ jayson@pi-jayson.local:~/pi-turret-v2/
ssh jayson@pi-jayson.local
```

## Model build/export — on the Strix Halo box ONLY (verify current Ultralytics flags first)
```
yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=bird.yaml nms=False
# the compiled file MUST end _edgetpu.tflite or the runtime silently uses CPU
```

## Git / GitHub
```
git status / git log --oneline -10
gh ...
```

## Darwin (Mac) notes
- BSD coreutils. Hardware libs (`picamera2`/`RPi.GPIO`/`smbus`) won't import — expected; mock them in tests.
- `python` (3.x) on the Mac vs `python3` on the Pi.
