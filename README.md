# pi-turret
Raspberry 4 - based AI autonomous laser turret python project

## Description
The goal is to provide ability for basic hardware compatibple with raspberry PI  to serve as a platform for autonomous selected target recognition, tracing and firing using available video cameras to capture frames 
DNN models to detect specified classes of objects in frames, translate detected object coordinates to motion of servo drives (2 DoF platform) and trigger mounted laser diod.

Project consists of following parts: 
  1) Local python server based on Bottle framework that allows remote control and provides turret data and live streaming from USB webcamera
  2) Turret servo driver (PCA9685) and image frame objects neural net detection handler module (v1/TurretHandler.py) .
  3) Pre trained neural network weigts (yolo8, mobilnet-ssd) for specified targets detection (v1/YOLOv8.py)
  4) Streaming server (mjpg-streamer) to handle USB webcamera real-time streaming


### Running
The v1 implementation lives under `v1/`. Run it from inside that folder:
``` cd v1 && python3 main.py ```

This will start Bottle WSGI server that will be exposed locally by current raspberry IP address on port 8001, eg: `http://192.168.68.145:8001/`  or by current host name, eg `http://<raspberry-host>.local:8001/` 
On the browser page the turret state and live stream through wecam is available. The autodetection is disabled by default and can be enabled via UI button. Servo controls are available only when autodetection is OFF

<img width="697" alt="Screenshot 2023-10-19 at 12 47 36" src="https://github.com/sukhov-work/pi-turret/assets/58325577/bdfc3588-f104-43b9-99ce-b6a46d39eacf">



### Hardware components
![image](https://github.com/sukhov-work/pi-turret/assets/58325577/d1cbdeec-c369-4b96-a6e8-ec957d1aa91a)



### Software Prerequisites 

 * Raspberry Pi OS ( 64 bit)
 * Python 3.9+
 * Open CV >= 4.4 , compiled for Pi OS - https://github.com/opencv
 * Ultralitics runtime - https://github.com/ultralytics/ultralytics
 * ONNX runtime (python) 
 * picamera2 - https://github.com/raspberrypi/picamera2
 * mjpg-streamer (optional, included in this repo with prebuilt opencv module) -  https://github.com/jacksonliam/mjpg-streamer 


### Training flow and data  
Getting started - https://blog.roboflow.com/how-to-train-yolov8-on-a-custom-dataset
ONNX format - https://github.com/ibaiGorordo/ONNX-YOLOv8-Object-Detection

Final Yolo trained model has been converted to ONNX and quantized to gain speed on per frame detection times ( ~ 3x improvement, more CPU friendly ) 

Main custom yolo8 model training flow: https://colab.research.google.com/drive/1j6nrV2YI72Dps6nHEw-J-5dTGIR1PTq1?usp=sharing
Model used in current detection flow is based on above training results in ./v1/models/v8_pigeon_best_384_int.onnx 

Pigeons dataset used for training (pigeon, crow, magpie classes):  https://universe.roboflow.com/jayson-x-an0sg/pigeons-h30dy



## v2 (in development)

v2 is a ground-up rebuild for a **water-cannon** deterrent that detects **any bird**, tracks and
predicts it, and fires non-blocking, with live multi-target handling and headless operation. **v1
stays the rollback and is never edited in place** — v2 is built alongside it at the repo root, while
v1 lives under `v1/`. Design + build details: `.claude/claude-docs/` (`V2-design-plan.md`,
`IMPLEMENTATION_PLAN.md`, `pi-turret-v1-legacy-design.md`); coding standards: `.claude/conventions/`.

### Layout (repo root = v2 root)
`config.py` `contracts.py` `errors.py` `capture.py` `main.py` + packages `detect/ track/ strategy/
aim/ actuate/ app/` + `tests/`. Imports are top-level (`from detect import …`).

### Three machines
- **Mac** — author code + run pure-logic `pytest`. No hardware/TPU truth.
- **Strix Halo (x86-64)** — train/export/INT8 + `edgetpu_compiler` (the only box that compiles Coral models).
- **Pi 4 (Bullseye, Python 3.9)** — the only source of camera/Coral/servo/FPS/aiming truth.

### Build & run
```bash
# tests (Mac): pure-logic spine — decode, NMS, tracker, scoring, controller, state machine, LCD, ...
python3 -m venv .venv-v2 && .venv-v2/bin/pip install -r requirements-v2.txt
.venv-v2/bin/python -m pytest -q

# deploy to a box (Mac = source of truth): git push-to-deploy, NOT rsync
git push pi main      # checks the repo out into ~/pi-turret on the Pi
ssh pi 'cd ~/pi-turret && python3 main.py'
```
The Pi/Strix boxes are reached over Tailscale via `ssh pi` / `ssh strix` (keys/hosts in `.claude/.env`,
gitignored); each box is a non-bare git repo that checks out on push (`receive.denyCurrentBranch=updateInstead`).
Prefer `mosh` + `tmux` for long sessions; rsync/scp is for large artifacts only (models, datasets). Fire is
**disabled by default** (`fire.enabled: false` in `config.yaml`) — "would-fire" telemetry only, for safe
bring-up. All tunables live in `config.py` / `config.yaml`.

### Run as a service + monitoring (on the Pi)
The app can run by hand (`python3 main.py`) or as a **manual-start systemd unit** (boots DISARMED;
stops cleanly via SIGTERM → servos centered, pump OFF):
```bash
sudo systemctl start turret      # not enabled on boot — start it yourself
journalctl -u turret -f          # live logs
sudo systemctl stop turret
```
**Grafana Alloy** on the Pi ships system metrics, logs, and log-derived turret telemetry
(`turret_fire_events_total`, aim error, state/target events) to **Grafana Cloud**. Config, importable
dashboards, and the full ops doc live in **`monitoring/`** (start at `monitoring/README.md`); the
Grafana Cloud token stays in `/etc/alloy/secrets.env` (never committed).

### Wiring (FIXED — reused from v1, do NOT rewire; escalate first)
| Function | Bus / pin |
|---|---|
| PCA9685 servo driver | I2C **bus 1** @ `0x40` |
| 1602A LCD (status display) | I2C **bus 1** (`rpi_lcd`) |
| Pan / Tilt servo | PCA9685 **ch 1 / ch 0** |
| Water pump (was v1 "main laser") | GPIO **BCM 26** (via relay/MOSFET + flyback diode) |
| Aux laser / aim marker (opt-in) | GPIO **BCM 27** |
| Status LED | GPIO **BCM 23** |
| IR receiver (PROPOSED, additive) | GPIO **BCM 17** (free pin; confirm before wiring) |

The **LCD** shows live lifecycle info (boot + IP, state, selected target, aim error, kill-zone, fps,
shot count). An **IR remote** (start/stop + basic control) is under consideration — additive on a free
pin via `dtoverlay=gpio-ir`; see `IMPLEMENTATION_PLAN.md` step 1.15.



