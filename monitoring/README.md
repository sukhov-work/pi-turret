# pi-turret monitoring — Grafana Alloy → Grafana Cloud

Final system state, data model, workflows, and dashboards for the Pi monitoring stack.
Stood up 2026-06-29 (replaces the dead Grafana Agent that was cleaned out the prior session).

---

## 1. What this is

**Grafana Alloy v1.17.0** runs on the Pi (`pi-jayson`) and ships **system metrics** (node_exporter),
**logs** (journald + `/var/log`), and **log-derived turret telemetry** to **Grafana Cloud** (free tier).
The turret app runs as a **manual-start systemd service** so its liveness is monitorable.

```
                              ┌──────────────────────── Pi (pi-jayson, Bullseye) ───────────────────────┐
  turret.service ──stdout──▶ journald ─┐                                                                 │
  (python3 main.py)                    │                                                                 │
  /var/log/*.log ──────────────────────┼──▶ Alloy v1.17 (/etc/alloy/config.alloy, 127.0.0.1:12345)       │
  node_exporter (in Alloy) ────────────┘        ├─ prometheus.exporter.unix (+systemd collector)         │
                                                ├─ loki.source.journal ─▶ loki.process "turret"          │
                                                │      └─ stage.match {unit="turret.service"} → metrics  │
                                                ├─ loki.source.file (/var/log/{syslog,messages,*.log})   │
                                                └─ prometheus.scrape self:12345 (turret_* + alloy_build) │
                              └─────────────────────────────────┬──────────────────────────────────────┘
                                                                │ remote_write / loki push (basic_auth)
                                                                ▼
                              Grafana Cloud  (stack mellowmushroom1792, region prod-us-west-0)
                                Prometheus (user 3291396)   Loki (user 1641373)
```

## 2. Deployed artifacts (what lives where)

| On the Pi (active) | In this repo (source of truth) | Notes |
|---|---|---|
| `/etc/alloy/config.alloy` | `monitoring/config.alloy` | the pipeline (env-driven) |
| `/etc/alloy/secrets.env` (chmod 600, **not in git**) | `monitoring/secrets.env.example` | endpoints + token |
| `/etc/default/alloy` | `monitoring/default-alloy` | pins HTTP to :12345, WAL to `/var/lib/alloy/data` |
| `/etc/systemd/system/alloy.service.d/override.conf` | `monitoring/systemd/alloy-override.conf` | loads secrets.env |
| `/etc/systemd/system/turret.service` | `monitoring/systemd/turret.service` | **manual-start, not enabled** |
| `/etc/systemd/system/turret-remote.service` | `monitoring/systemd/turret-remote.service` | IR supervisor (root) — **enabled on boot** |
| `/etc/systemd/system/pi-turret-ir.service` | `monitoring/systemd/pi-turret-ir.service` | IR keytable loader (oneshot) |
| `/etc/rc_keymaps/pi_turret.toml` | `monitoring/rc_keymaps/pi_turret.toml` | NEC scancode → KEY_* map |
| — | `monitoring/ir-load-keytable.sh` | resolves rc device by name + loads keytable |
| — | `monitoring/dashboards/*.json` (+ generator) | import into Grafana |
| — | `monitoring/deploy.sh` | re-deploy config to /etc |

**Service state:** `alloy` = enabled + running (monitoring persists across reboot).
`turret.service` = installed, **disabled, stopped** (start it yourself, or via the IR power key; see §5).
`turret-remote.service` = IR supervisor that owns the receiver and `systemctl start/stop`s turret.service for the
power key (forwards other keys to the app's :8001 API); **enabled on boot**, self-exits when `remote.enabled=False`.

## 3. Grafana Cloud connection (stack `mellowmushroom1792`, org 1804779, region prod-us-west-0)

| | URL | username (instance ID) |
|---|---|---|
| Prometheus remote_write | `https://prometheus-prod-67-prod-us-west-0.grafana.net/api/prom/push` | `3291396` |
| Loki push | `https://logs-prod-021.grafana.net/loki/api/v1/push` | `1641373` |
| Grafana | `https://mellowmushroom1792.grafana.net` | (browser login) |

Auth = basic_auth(username, **token**). Token is a Cloud Access Policy token (policy `pi-turret`,
scopes incl. `metrics:write`/`logs:write`). Stored **only** in `/etc/alloy/secrets.env` and `.claude/.env`
(both gitignored). The numeric IDs/URLs were discovered via the grafana.com API (`stacks:read`).

## 4. Data model (labels & metrics — what to query)

Common labels: `instance="pi-jayson"`, `job="integrations/raspberrypi-node"` (system+logs) /
`job="integrations/agent-check"` (Alloy self). Scrape interval 60s.

**System metrics** — standard `node_*` (cpu, memory, filesystem, diskstats, netdev, hwmon temp,
loadavg) + `node_systemd_unit_state{name=~"turret.*|alloy.service"}` for liveness.

**Turret telemetry** (log-derived; clean names — Alloy's `loki_process_custom_` prefix is renamed away):
| Metric | Type | Labels | Source log line |
|---|---|---|---|
| `turret_fire_events_total` | counter | instance, job | `FIRE #53 (shot 1, aim_err=7px)` |
| `turret_aim_error_px` | gauge | instance, job | (last fire's aim_err) |
| `turret_aim_error_px_hist` | histogram | le | (aim_err distribution) |
| `turret_state_transitions_total` | counter | `to_state` | `state aiming -> firing` |
| `turret_target_events_total` | counter | `event` (acquired/lost/switch) | `target acquired #53` |

> Cardinality discipline: `target_id` (#53), `from_state`, `shot`, PID, `boot_id` are **kept out of
> labels** on purpose. Only the bounded `to_state`/`event` sets are promoted. Don't add unbounded labels.

**Logs** (Loki) — query `{instance="pi-jayson"}`; useful filters: `{unit="turret.service"}`,
`{job="integrations/raspberrypi-node"} |= "FIRE"`, `... | level=~"error|warning"`.

## 5. Workflows

**Start / stop the turret (manual):**
```bash
ssh pi
sudo systemctl start turret      # boots DISARMED (no fire until you Arm in the web UI :8001)
systemctl status turret
sudo systemctl stop turret       # SIGTERM → main.py disarms (servos centered, pump OFF)
journalctl -u turret -f          # live logs (also go to Loki)
```
It is intentionally **not** enabled on boot — a later phase (IR remote, `ir-remote-integration-plan.md`)
adds a supervisor to manage it. To auto-start anyway: `sudo systemctl enable turret`.

**Change the Alloy config:** edit `monitoring/config.alloy` on the Mac → `git push pi main` →
`ssh pi ~/pi-turret/monitoring/deploy.sh` (copies to /etc, validates, reloads). Or hand-edit
`/etc/alloy/config.alloy` then `sudo systemctl reload alloy`.

**Import the dashboards:** Grafana → Dashboards → New → **Import** → upload
`monitoring/dashboards/pi-health.json` and `turret-telemetry.json` → pick your Cloud Prometheus + Loki
data sources when prompted. (Regenerate after edits: `python3 monitoring/dashboards/generate_dashboards.py`.)

**Rotate the token:** new Cloud Access Policy token → update `GRAFANA_CLOUD_TOKEN` in
`/etc/alloy/secrets.env` → `sudo systemctl restart alloy`. (Also update `.claude/.env` if scripts use it.)

**Troubleshoot:**
```bash
ssh pi
systemctl status alloy; journalctl -u alloy -f         # service + live logs
curl -s localhost:12345/metrics | grep -E 'turret_|up '  # what Alloy exposes locally
```
Verify ingestion from the Mac (token from `.claude/.env`):
```bash
TOKEN=$(grep '^GRAFANA_TOKEN=' .claude/.env | cut -d= -f2-)
curl -s -u "3291396:$TOKEN" -G https://prometheus-prod-67-prod-us-west-0.grafana.net/api/prom/api/v1/query \
  --data-urlencode 'query=up{instance="pi-jayson"}'
```

## 6. Free-tier budget (verified headroom)

Free tier = 10k active series / 50 GB logs / 14-day retention. Current: **~740 active series**
(plenty). First start backfills ~24 h of journal (one-time log spike), then incremental. If you ever
approach limits: drop the journal `boot_id` relabel, tighten `loki.source.file` globs to just the
turret log, or add a node_exporter keep-list.

## 7. Known issues / observations

- **`/var/log/boot.log: permission denied`** in Alloy logs — that one file is root-only; harmless,
  all other `/var/log` files tail fine. Exclude it from the glob if the noise bothers you.
- **Turret disarm traceback** (`gpiozero ... OutputDevice is closed`) on `systemctl stop` — a
  pre-existing v2 shutdown double-close; the unit still stops cleanly (exit 0). Unrelated to monitoring.
- **FIRE metrics unverified live** — `turret_fire_events_total`/`turret_aim_error_px*` use the exact
  same pipeline branch as the verified `state`/`target` metrics; they'll populate on the first real
  fire (not tested here to avoid spraying water unattended).
- **Dashboards are import-only** — the Cloud Access Policy token can't write to the Grafana instance
  API (that needs a Grafana service-account token). Import the JSON, or mint an SA token later to script it.

## 8. Future improvements

- Alerts: turret/alloy down (`node_systemd_unit_state == 0`), CPU temp > 80 °C, disk > 90 %, no-data on `up`.
- Per-process CPU/mem via `prometheus.exporter.process` (match `main.py`) once you want resource trends.
- `vcgencmd get_throttled` undervoltage/throttle bits via a node_exporter textfile-collector script.
- ✅ IR-remote supervisor (`turret-remote.service`) landed 2026-06-30 — already matched by the systemd
  collector `unit_include="(turret.*|alloy)\.service"` (no Alloy change) + a Services liveness row in
  `pi-health.json`. Consider an SA token to provision dashboards/alerts as code.
