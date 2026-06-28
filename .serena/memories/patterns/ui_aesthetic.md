# mem:patterns/ui_aesthetic — pi-turret UI design language

**Owner preference (durable):** the operator UI must read like a **serious, minimalistic,
real-world mil-tech / fire-control product** — an instrument panel, not a web app or a gamer
RGB skin. Apply this to ALL pi-turret UI work. (Authority: `app/web_ui.html` — re-read it; this
memory is the intent behind it.)

## Principles
- **Minimal + dense + purposeful.** Every element earns its place; no decoration for its own sake.
  Information-forward, glanceable, efficient. Operator must read system state in <1s.
- **State-forward.** The FSM state, SYS ARMED/SAFE, and FIRE LIVE/SAFE are the most prominent
  things on screen. FIRING pulses red. Danger actions (fire-live) are visually distinct (red).
- **Instrument look:** monospace everywhere, UPPERCASE labels w/ letter-spacing, **sharp corners**
  (no rounded pills), thin 1px borders, `//` / `▸` section markers, subtle top accent line.
- **Tactical, not neon.** Phosphor-green-on-near-black; amber = caution/aiming; red = fire/danger;
  cyan = prediction/velocity. Muted, not saturated.

## Palette (CSS vars in `app/web_ui.html`)
bg `#080b09` · panel `#0d1210` · line `#1b271f` · dim `#5f7568` · txt `#a9c0b0` · bright `#dfeee4`
· green `#3fbf6f` · amber `#d6a626` · red `#e0492f` · cyan `#36a9b8`. Font = ui-monospace stack.

## Layout (3-col, collapses to 1 on narrow)
- **Header status strip:** brand `PI-TURRET // FCS`, STATE, TRACKS, RATE(fps), SHOTS, then SYS /
  FIRE / LINK tags. Colour-coded by state. LINK tag flips to "LINK LOST" if telemetry stalls >1.5s.
- **Left = Fire Control** (arm/disarm/fire-live/fire-safe/center/toggle), **Manual Slew** (jog pad,
  **disabled unless disarmed** — buttons greyed + note), **Optics/Stream** (feed on/off, marker on/off).
- **Center = Tactical canvas** (detection-frame pixel space: grid + center reticle + kill-zone
  [amber, dashed; fills red + caption "KZ·LOCK" when would_fire], tracks as boxes w/ #id, selected
  = brighter + ring, velocity vectors cyan, predicted-lead = cyan X) + **Visual** (USB `<img>` feed).
- **Right = Telemetry readout** (target/aim-err/in-KZ/would-fire/pan/tilt/lead), **Track Table**,
  **Parameters** (collapsible per-section live tuning).

## Data contract the UI depends on (`TurretWebController.telemetry()`)
Needs every poll: `state, armed, fire_enabled, fps, shots, num_tracks, selected_target_id,
aim_error_px, predicted_xy, in_killzone, would_fire, pan/tilt_cmd_deg, tracks[], stream{}`,
**plus `frame{w,h}` and `killzone{}`** for the tactical canvas. Poll @ ~300 ms; canvas redrawn
each poll. Stream URL is built **host-relative** (`location.hostname:stream.port`) — never hardcode.

Related: `mem:architecture/v2_scaffold` (web layer), `mem:core`.
