# mem:suggested_commands

Cheat-sheet. Each command notes its machine (see `mem:project/dev_environment`; access details in
`mem:project/machine_access`). Real hosts/users/key live in `.claude/.env` (gitignored — never commit/share).

## Reach the boxes (Tailscale SSH from the Mac)
```
ssh pi          # alias (resolves via ~/.ssh/config + .claude/.env)
ssh strix       # alias
# interactive / long work — prefer mosh + tmux (survives drops):
mosh pi ; tmux new -A -s turret
# long builds detached so they outlive the SSH session:
ssh strix 'tmux new -A -d -s build "<cmd>"'      # reattach: ssh strix -t tmux attach -t build
```

## Run v1 (rollback reference) — on the Pi
```
cd v1 && python3 main.py        # Bottle UI on :8001 (v1's paths are relative to v1/)
```

## Tests (pure logic) — on the Mac
```
python -m pytest tests/ -v
python -m pytest -m "not hardware and not slow" -v    # fast subset
```

## Deploy = git push-to-deploy (Mac is source of truth; NOT rsync)
```
git push pi main        # checks out into ~/pi-turret on the Pi
git push strix main     # checks out into ~/pi-turret on Strix
# each box repo has receive.denyCurrentBranch=updateInstead; .env is gitignored so secrets never ship.
# rsync/scp ONLY for big artifacts (models, images, datasets, the compiled _edgetpu.tflite).
```

## Model build/export — on the Strix Halo box ONLY, via `ssh strix` (verify current Ultralytics flags first)
```
yolo export model=best.pt format=edgetpu imgsz=256 int8=True data=bird.yaml nms=False
# the compiled file MUST end _edgetpu.tflite or the runtime silently uses CPU.
# NOTE: edgetpu_compiler not yet installed on Strix (Ubuntu/x86-64, Py3.13) — set up in a venv/Docker first.
```

## Git / GitHub
```
git status / git log --oneline -10
gh ...
```

## Darwin (Mac) notes
- BSD coreutils. Hardware libs (`picamera2`/`RPi.GPIO`/`smbus`) won't import — expected; mock them in tests.
- `python` (3.x) on the Mac vs `python3` on the Pi. tmux on the Mac + both boxes; mosh-server on both boxes.
