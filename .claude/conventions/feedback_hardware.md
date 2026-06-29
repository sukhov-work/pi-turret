---
name: Hardware wiring is fixed — reuse v1 pins, escalate before rewiring
description: pi-turret owner's rule on physical wiring/GPIO changes and how richly to use the LCD
type: feedback
originSessionId: 2d1c6e6b-c69a-408b-97a3-c947eb1d3ad7
---
Treat the turret's **physical wiring as FIXED**: reuse v1's exact GPIO/I2C pins, relays, diodes, and
breadboard layout in v2. Never silently change a pin assignment or propose rewiring relays/diodes/the
breadboard without a serious reason — **escalate immediately and confirm first**. New hardware (e.g.
the proposed IR receiver) is **additive on FREE pins only**, and even then flag the chosen pin for
confirmation before assuming it.

**Why:** The rig is already physically built/soldered; redoing wiring is costly and error-prone in a
way code changes are not. The owner stated this explicitly while reviewing v2 wiring.

**How to apply:** Before writing or changing anything that touches servos, the pump, LEDs, the LCD, or
a new sensor, check the as-built pin map first (`mem:architecture/wiring`, verified from
`v1/TurretHandler.py` + `v1/PCA9685.py`) and match it. v1 pins: pump=BCM26, aux laser=BCM27,
status LED=BCM23, PCA9685=I2C bus 1 @ 0x40, 1602A LCD on the same I2C bus 1, pan=PCA9685 ch1/tilt=ch0.
**v2 divergence (owner-rewired 2026-06-29): the aux laser/marker is on BCM24, not v1's BCM27** — v1 code
still drives BCM27 (now disconnected). All other pins match v1. If a task seems to need a pin/wiring change, stop and ask.

**Related preference — use the LCD richly:** the owner wants the 1602A LCD to surface as much useful
lifecycle info as possible throughout the run (boot+IP, state, selected target, aim error, kill-zone,
fps, shot count), not just on/off. Keep it fail-safe (LCD errors must never stop the turret).
