# mem:decisions/monitoring — Pi observability (Grafana Alloy → Grafana Cloud)

Stood up 2026-06-29, replacing the dead Grafana Agent (cleaned out the prior session). **Full
ops doc is in-repo: `monitoring/README.md`** (read it first). This memory = the durable, non-obvious
bits + the gotchas a future session would otherwise re-discover the hard way.

## What runs where
- **Alloy v1.17.0** on the Pi (`pi-jayson`), installed from `apt.grafana.com` (repo+key added fresh — it
  was absent). **enabled + running** (monitoring survives reboot). HTTP/self-metrics on `127.0.0.1:12345`.
- Config = **`monitoring/config.alloy`** (repo, source of truth) → deployed to `/etc/alloy/config.alloy`.
  Env-driven; creds in **`/etc/alloy/secrets.env`** (root, chmod600, **gitignored** — never commit).
  `/etc/default/alloy` pins :12345 + WAL `/var/lib/alloy/data`; `…/alloy.service.d/override.conf` loads secrets.
- **`turret.service`** = `User=jayson`, `/usr/bin/python3 /home/jayson/pi-turret/main.py`,
  **manual-start: installed but NOT enabled** (owner wants manual control; a later IR-remote supervisor —
  see `ir-remote-integration-plan.md` — will manage it). Boots DISARMED. `sudo systemctl start turret`.
- Redeploy after a config change: `git push pi main` → `ssh pi ~/pi-turret/monitoring/deploy.sh`.

## Grafana Cloud (NOT secret — like tenant IDs; the token IS secret)
- Stack `mellowmushroom1792`, org `1804779`, region **prod-us-west-0** (the dead account was us-east — different).
- Prometheus remote_write `https://prometheus-prod-67-prod-us-west-0.grafana.net/api/prom/push`, user **3291396**.
- Loki push `https://logs-prod-021.grafana.net/loki/api/v1/push`, user **1641373**.
- Auth = Cloud Access Policy token (policy `pi-turret`), in `.claude/.env` `GRAFANA_TOKEN` + `/etc/alloy/secrets.env`.
- **Token scope rule:** data-plane scopes (`metrics:write`/`logs:write`) ship data but **403** on
  `grafana.com/api/instances` — need **`stacks:read`** to discover the IDs/URLs above. The token **cannot**
  write the Grafana **instance** API (401) → dashboards are import-only unless you mint a Grafana SA token.

## Alloy v1.17 gotchas (verified on-Pi; the migrate doc missed these)
- `loki.process` `stage.metrics` are exposed **prefixed `loki_process_custom_<name>`** AND **inherit the
  journal stream labels** (unit/transport/boot_id/level). Old Grafana Agent did neither.
- Fix pattern (in `config.alloy`): wrap extraction in `stage.match {unit="turret.service"}` (else generic
  regexes like `state \w+ -> \w+` match **tailscaled** logs); promote only bounded `to_state`/`event`; then a
  `prometheus.scrape` of `:12345` (`honor_labels=true`) → `prometheus.relabel` that **keeps**
  `loki_process_custom_turret_*`, **renames** → clean `turret_*`, and **labeldrops** the inherited labels.
- `node_exporter` is embedded (`prometheus.exporter.unix`); systemd collector `unit_include="(turret.*|alloy)\.service"`
  gives `node_systemd_unit_state{name="turret.service",state="active"}` for liveness.

## Data model (what to query)
- Labels: `instance="pi-jayson"`, `job="integrations/raspberrypi-node"` (system+logs) / `integrations/agent-check` (self).
- Turret metrics (clean names, verified in Cloud): `turret_fire_events_total`, `turret_aim_error_px`,
  `turret_aim_error_px_hist`, `turret_state_transitions_total{to_state}`, `turret_target_events_total{event}`.
  Regexes match v2 logs `app/pipeline.py:120/125`, `app/control.py:132/134/136`. **Never** label by
  `target_id`/PID/`boot_id` (unbounded). ~740 active series total (10k free-tier cap — lots of headroom).
- FIRE metrics unverified-live (same pipeline branch as the verified state/target; not fired to avoid water).

## Dashboards
- Importable JSON: `monitoring/dashboards/{pi-health,turret-telemetry}.json` (built by `generate_dashboards.py`,
  datasource-prompted on import). Not auto-pushable with the current token.

Related: `mem:core`, `mem:project/machine_access`, repo `monitoring/README.md`, `DECISIONS.md` (2026-06-29 entries).
