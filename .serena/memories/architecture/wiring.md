# mem:architecture/wiring — FIXED GPIO/I2C map (do not rewire)

The owner does NOT want the breadboard/relays/diodes/pins rewired without a serious
reason. **Escalate before any rewire.** New hardware is additive on FREE pins only,
and still flag it for confirmation. v2 reuses every v1 pin exactly.

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
| Aux laser / aim marker | GPIO **BCM 27** (opt-in, `gpiozero.LED`) | `actuate/indicators.py` |
| Status LED | GPIO **BCM 23** (`gpiozero.LED`) | `actuate/indicators.py` |
| IR receiver (PROPOSED) | GPIO **BCM 17** (free; CONFIRM before wiring) | `app/remote.py` |

Free pins besides BCM17: 4/5/6/12/13/16/18/19/20/21/22/24/25 + SPI block. BCM 2/3 = I2C; 23/26/27 used.
**v1 has NO GPIO inputs** — so an IR receiver is purely additive.

## LCD usage (v2 extends v1)
v1 showed only on/off + angles. v2 surfaces lifecycle info on the 16x2: boot + LAN IP, then per state —
SEARCHING `SCAN <spin> <fps> / trk:N ARM|SAFE`, AIMING `AIM#id e<err> / KZ:Y WF ARM`, FIRING
`FIRE! #id / shots:N`, COOLDOWN, SAFE. Rendered by a low-rate thread (`app/display.py::LcdReporter`,
`format_lcd_lines` pure) so I2C never blocks control. `rpi_lcd.LCD.text(msg, row)` rows are 1-indexed.

## Fail-safe rule
LCD + indicators swallow hardware errors (log + continue) — a flaky display/LED never stops the turret.
Status LED on while not SAFE; aux laser marker OFF unless `app.aux_marker_enabled`. All OFF on disarm.

## IR remote (PROPOSED, step 1.15)
rc-core/evdev via `dtoverlay=gpio-ir,gpio_pin=17` → remote appears as `/dev/input/eventN`; capture keys
with `ir-keytable -t` into `RemoteConfig`. Actions: arm/disarm, toggle fire-enable, center, jog pan/tilt
(+ an IR e-stop is a cheap safety win). Seam: `app/remote.py` (`RemoteActions`/`build_key_map`/
`RemoteListener`, evdev lazy). GATE: owner confirms pin + captures codes on the Pi.
