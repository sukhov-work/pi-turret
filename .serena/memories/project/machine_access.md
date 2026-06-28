# mem:project/machine_access

How the Mac reaches + operates the Pi and Strix boxes, and deploys to them. **Anonymized** — the real
hosts/users/key-path/working-dirs/passwords live ONLY in `.claude/.env` (gitignored; never commit/share).
(Referred by `mem:core`, `mem:project/dev_environment`.)

## Reach the boxes (Tailscale SSH from the Mac)
- `~/.ssh/config` aliases **`ssh pi`** and **`ssh strix`** use a dedicated Tailscale key automatically
  (no `-i`); other hosts (e.g. `github.com`) keep their own keys. Explicit form:
  `ssh -i "$MAC_SSH_KEY_LOCALTION" <user>@<host>` — every value comes from `.claude/.env`.
- The key is passphrase-protected on disk but unlocked in the Mac ssh-agent, so non-interactive ssh
  works (BatchMode ok). In scripts add `-o IdentitiesOnly=yes` to force only that key.
- Prefer **mosh + tmux** for interactive/long work (both boxes have mosh-server + tmux; the Mac has tmux):
  `mosh pi`, then `tmux new -A -s turret`. Run long builds in a **detached tmux on the box** so they
  survive drops: `ssh strix 'tmux new -A -d -s build "<cmd>"'`; reattach `ssh strix -t tmux attach -t build`.

## Deploy = git push-to-deploy (Mac is source of truth; NOT rsync)
- Both boxes hold a non-bare repo at `~/pi-turret` with `receive.denyCurrentBranch=updateInstead`;
  `git push pi main` / `git push strix main` from the Mac checks out the tree. One-directional (Mac → box).
- `.env` is gitignored, so a push never carries secrets to a box. GitHub `origin` is a mirror.
- **rsync/scp only for big artifacts** (test models, images, datasets, the compiled `_edgetpu.tflite`) —
  never tracked source; pull results back, then commit on the Mac.

## Roles & cautions
- **Mac** = author/test/git, source of truth. **Strix** (`ssh strix`; x86-64 Ubuntu, Py 3.13 system) =
  train/export/INT8/`edgetpu_compiler` — the only box that compiles Coral models. **Pi** (`ssh pi`;
  Bullseye, Py 3.9) = the only hardware/FPS/aiming truth.
- **Strix is shared/critical:** never upgrade its OS or make global/destructive system or Python package
  changes — confirm with the owner first; venvs are fine. `edgetpu_compiler` is **not yet installed**
  there (set it up in a venv/Docker before the compile phase).
- Authorize the key on a new box with `ssh-copy-id` using the Mac key (details in `.claude/.env`).
