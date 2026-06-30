# mem:architecture/wiring — FIXED GPIO/I2C map (do not rewire)

The owner does NOT want the breadboard/relays/diodes/pins rewired without a serious
reason. **Escalate before any rewire.** New hardware is additive on FREE pins only,
and still flag it for confirmation. v2 reuses every v1 pin **except the owner-rewired
aux laser/marker** (moved to its own pin **BCM24** on 2026-06-29; v1 code still drives BCM27).

Verified from source: `v1/TurretHandler.py:40-51`, `v1/PCA9685.py:32`. (Related: `mem:core`,
`mem:architecture/v2_scaffold`.)

## Map (as-built)
| Function | Bus / pin | v2 owner |
|---|---|---|
| PCA9685 servo driver | I2C **bus 1** @ `0x40` | `actuate/pca9685.py` |
| 1602A LCD | I2C **bus 1** (`rpi_lcd`, ~`0x27`) — shares bus, no conflict | `actuate/lcd.py` |
| Pan servo (MG996R) | PCA9685 **ch 1** | `ServoConfig.pan_channel` |
| Tilt servo (MG996R) | PCA9685 **ch 0** | `ServoConfig.tilt_channel` |
| Water pump (v1 "main laser") | GPIO **BCM 26** (relay/MOSFET + flyback) | `actuate/pump.py` |
| Aux laser / aim marker | GPIO **BCM 24** — rewired from v1's BCM27 (opt-in, `gpiozero.LED`) | `actuate/indicators.py` |
| Status LED | GPIO **BCM 23** (`gpiozero.LED`) | `actuate/indicators.py` |
| IR receiver (WIRED) | GPIO **BCM 25** / pin 22 (`dtoverlay=gpio-ir,gpio_pin=25`) — owner-wired 2026-06-30, supersedes BCM17 | `remote_daemon.py` / `app/remote_supervisor.py` |

Free pins besides BCM25: 4/5/6/12/13/16/17/18/19/20/21/22 + SPI block (**BCM27 freed by the aux rewire**). BCM 2/3 = I2C; 23/24/25/26 used.
**v1 has NO GPIO inputs** — so an IR receiver is purely additive.

## LCD usage (v2 extends v1)
v1 showed only on/off + angles. v2 surfaces lifecycle info on the 16x2: boot + LAN IP, then per state —
SEARCHING `SCAN <spin> <fps> / trk:N ARM|SAFE`, AIMING `AIM#id e<err> / KZ:Y WF ARM`, FIRING
`FIRE! #id / shots:N`, COOLDOWN, SAFE. Rendered by a low-rate thread (`app/display.py::LcdReporter`,
`format_lcd_lines` pure) so I2C never blocks control. `rpi_lcd.LCD.text(msg, row)` rows are 1-indexed.

## Fail-safe rule
LCD + indicators swallow hardware errors (log + continue) — a flaky display/LED never stops the turret.
Status LED on while not SAFE; aux laser marker OFF unless `app.aux_marker_enabled`. All OFF on disarm.

## IR remote (Step 1.15 — SUPERVISOR daemon, built 2026-06-30)
Receiver on **BCM25** (`dtoverlay=gpio-ir,gpio_pin=25`) → rc-core/evdev → `/dev/input/eventN`. Architecture is
a **separate always-on daemon** `turret-remote.service` (root) = `remote_daemon.py` → `app/remote_supervisor.py`:
owns the IR device (EVIOCGRAB), POWER key (`0`) → `systemctl start/stop turret.service`, every other key →
HTTP POST to the running app's :8001 web API (`/api/cmd`, `/api/control-cmd`). It NEVER drives servos/pump
(control thread stays the single mover). The in-process `app/remote.py` listener is a dormant seam. Keytable
`monitoring/rc_keymaps/pi_turret.toml` loaded by `pi-turret-ir.service` (resolve rc device by name `gpio_ir_recv`).
Capture this remote's real NEC scancodes with `ir-keytable -t` on the Pi. Ships `remote.enabled=False`.
