# mem:suggested_commands

Cheat-sheet. Each command notes its machine (see `mem:project/dev_environment`; access details in
`mem:project/machine_access`). Real hosts/users/key live in `.claude/.env` (gitignored — never commit/share).

## Reach the boxes (Tailscale SSH from the Mac)
```
ssh pi          # alias (resolves via ~/.ssh/config + .claude/.env)
ssh strix       # alias
# if `ssh pi` TIMES OUT on a stale control socket, bypass it (verified 2026-06-29):
ssh -o ControlMaster=no -o ControlPath=none pi '<cmd>'
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
.venv-v2/bin/python -m pytest -q                      # the actual v2 runner (Py 3.9.6); 178 passed / 0 skipped
python -m pytest -m "not hardware and not slow" -v    # fast subset form
```

## Deploy = git push-to-deploy (Mac is source of truth; NOT rsync)
```
git push origin main ; git push pi main ; git push strix main   # push ALL THREE every deploy
# each box repo has receive.denyCurrentBranch=updateInstead; .env is gitignored so secrets never ship.
# committed models in models/ SHIP VIA THE PUSH (no rsync). rsync/scp ONLY for datasets/images/fixtures.
# push-to-checkout fails ("Could not update working tree") if a box has untracked files in the checked-out
# path (e.g. fixtures the generator wrote into the box's tests/fixtures/) — rm the identical file, re-push.
```

## Model build/export — on Strix ONLY (verify current Ultralytics flags first)
```
# edgetpu_compiler v16.0 is INSTALLED (apt global, on PATH). Export venv = ~/turret-ml (uv, Py 3.12).
~/turret-ml/bin/yolo export model=best.pt format=edgetpu imgsz=256 int8=True \
    data=~/turret-ml/datasets/pigeons-single-class/data.yaml nms=False dynamic=False
# gate: edgetpu_compiler -s -> "Number of Edge TPU subgraphs: 1", off-chip ~=0. File contains _edgetpu.
```

## On-Pi detector benchmark (Pi truth)
```
ssh -o ControlMaster=no -o ControlPath=none pi 'cd ~/pi-turret && python3 scripts/pi_detector_bench.py --image ~/bird.jpg --iters 200'
```
Full retrain/deploy loop (field data -> annotate -> train -> export -> golden fixture -> deploy -> measure):
**`.claude/claude-docs/MODEL_ITERATION.md`**.

## Git / GitHub
```
git status / git log --oneline -10
gh ...
```

## Darwin (Mac) notes
- BSD coreutils. Hardware libs (`picamera2`/`RPi.GPIO`/`smbus`) won't import — expected; mock them in tests.
- `python` (3.x) on the Mac vs `python3` on the Pi. tmux on the Mac + both boxes; mosh-server on both boxes.
