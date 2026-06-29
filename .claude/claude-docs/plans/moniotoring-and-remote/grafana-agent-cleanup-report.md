# Grafana Agent Cleanup — Execution Report (2026-06-29)

*Outcome of running `grafana-agent-cleanup-brief.md` against the live Pi. The box is now clean and
ready for the Alloy install in `migrate-to-alloy.md`. Read the "Findings that change the next session"
section before starting the migration — three of them correct the migrate doc.*

## Result: SUCCESS — system fully clean

The dead `grafana-agent` (which was **still running and spamming `401 Unauthorized` every ~1–2 s**)
is completely removed: no package, no unit, no binaries, no config/data, no user/group, no listening
ports. ~357 MB freed. The two config files are backed up on the Pi.

## Target (measured, read-only, before any change)

| Fact | Value |
|---|---|
| Host / user / home | `pi-jayson` / `jayson` / `/home/jayson` |
| OS / arch | Debian 11 Bullseye, aarch64 |
| sudo | passwordless (`sudo -n` works) |
| Install type | **dpkg package** `grafana-agent 0.36.1-1` (Sep 2023) → package-purge path (3a) |
| Service state | **active + enabled**, PID 640, up 3 h, actively erroring |
| HTTP/gRPC server | `127.0.0.1:9090` / `:9091` (via `CUSTOM_ARGS`) — **NOT :12345** |
| Binaries | `/usr/bin/grafana-agent` (187 MB), `/usr/bin/grafana-agentctl` (169 MB) |
| Config (pkg-owned conffile) | `/etc/grafana-agent.yaml` (owned `jayson:jayson`, hand-edited), `/etc/default/grafana-agent` |
| Data / WAL / positions | `/var/lib/grafana-agent` (28 K), `/tmp/grafana-agent-wal`, `/tmp/positions.yaml` |
| User / group | `grafana-agent` uid 999 / gid 995; supplementary groups **`systemd-journal`, `adm`** |
| Standalone collectors | **none** (no node_exporter / promtail / telegraf / collectd / cadvisor) |
| **apt repo `apt.grafana.com`** | **ABSENT** — no repo file, no GPG key anywhere |

## What was done (each brief step → outcome)

1. **Backup first (before any removal)** — copied `/etc/grafana-agent.yaml` → `~/grafana-agent.yaml.bak`
   and `/etc/default/grafana-agent` → `~/grafana-agent.default.bak` (chowned to `jayson`). Done **before**
   purge because apt purge removes the conffile.
2. **Step 2 — stop + disable**: `systemctl disable --now grafana-agent` → inactive; `:9090/:9091/:12345`
   all freed; the 401 spam stopped. (`grafana-agent-flow` absent → skipped.)
3. **Step 3a — package purge**: gated on `dpkg -l`, then `apt-get purge -y grafana-agent` →
   removed `grafana-agent (0.36.1-1)` + conffiles, **357 MB freed**, both binaries gone.
   - `apt-get autoremove -y` additionally removed **4 pre-existing orphan libs unrelated to the agent**:
     `cmake-data`, `libfuse2`, `libjsoncpp24`, `librhash0` (they were already "no longer required").
     Harmless and reversible: `sudo apt-get install cmake-data libfuse2 libjsoncpp24 librhash0` if ever needed.
4. **Step 4 — leftovers**: config/env already taken by purge; removed `/var/lib/grafana-agent`,
   `/tmp/grafana-agent-wal`, `/tmp/positions.yaml`; `userdel grafana-agent` (also removed its primary
   group and stripped it from `adm`/`systemd-journal`); `daemon-reload` + `reset-failed`.
5. **Step 5 — standalone collectors**: **N/A** (none were present).
6. **Step 6 — apt repo dedupe**: **N/A** — the repo was already absent (nothing to keep or dedupe).
   **Left untouched on purpose**; adding it is the next session's job.
7. **Step 7 — verify**: package ✓gone, unit ✓gone, binaries ✓gone, user/group ✓gone, ports ✓free,
   only residual files are the two intended `~/*.bak` backups, no fresh agent log lines.

## End state (verified)

- `dpkg -l | grep grafana-agent` → nothing. No `grafana-agent.service` unit on disk.
- `which grafana-agent grafana-agentctl` → nothing.
- `:12345`, `:9090`, `:9091` → all free (Alloy can bind :12345).
- `getent passwd/group grafana-agent` → removed.
- `apt.grafana.com` repo → **still absent** (next session adds it).
- Backups retained: `/home/jayson/grafana-agent.yaml.bak` (3127 B), `/home/jayson/grafana-agent.default.bak` (459 B).

## Findings that change the next session (READ THESE)

1. **The Grafana apt repo + key are ABSENT, not present.** The cleanup brief's #1 caveat ("do NOT
   remove the repo, Alloy needs it") did not apply — there was nothing to keep. So `migrate-to-alloy.md`
   §1 (install the repo + GPG key) is **mandatory, not redundant**. Run it verbatim. The old agent was
   evidently installed from a one-off `.deb`, then the repo was removed.
2. **`migrate-to-alloy.md` §4's `turret.service` uses the wrong path/user.** It hard-codes
   `WorkingDirectory=/home/pi/pi-turret` and `User=pi`. On this box the user is **`jayson`** and the repo
   is **`/home/jayson/pi-turret`**. Fix the unit accordingly. (Also note v2's entry point may not be a
   bare `main.py` at repo root — confirm the actual run command before writing the unit.)
3. **The turret log-derived metrics in `migrate-to-alloy.md` §3/§5 are GREENFIELD, not a translation.**
   The real agent config did **no** turret parsing — it only shipped journald + `/var/log/*` raw. The
   `FIRE #53 (shot 1, aim_err=7px)`, `state X -> Y`, `target acquired|lost|switch` regexes are an
   **assumed** format. **Verify the actual v2 turret log strings on the Pi** (the v2 app uses stdlib
   logging — see `app/control.py`, `app/pipeline.py`, `app/statemachine.py`) before writing
   `loki.process` stages, or those metrics stay empty.
4. **Old agent ran on `:9090/:9091`, not `:12345`.** So there was never a port clash. Alloy will use
   its default `:12345` (free). The optional debug-UI line in the migrate doc is fine as-is.
5. **The new `alloy` user must be in `systemd-journal` + `adm`** (the old agent was — confirmed via the
   `userdel` output). `migrate-to-alloy.md` §1 already says `usermod -aG systemd-journal,adm alloy` — keep it.
   - Minor: the old agent logged `open /var/log/boot.log: permission denied` even with `adm`. Alloy's
     `/var/log/{syslog,messages,*.log}` file scrape may hit the same on `boot.log`; ignore or exclude it.
6. **Self-metrics keep-list nuance:** the real config kept
   `(prometheus_target_.*|prometheus_sd_discovered_targets|agent_build.*|agent_wal_samples_appended_total|process_start_time_seconds)`.
   The migrate doc's Alloy keep-list drops `prometheus_sd_discovered_targets` and renames `agent_*`→`alloy_*`.
   Add `prometheus_sd_discovered_targets` back if you want parity; confirm the exact `alloy_*` names at
   `http://127.0.0.1:12345/metrics`.

## Old endpoints / credentials (dead — for reference only)

Confirmed against the live config; matches `migrate-to-alloy.md` §2. **Tokens were already revoked**
(that's the `401 invalid token` the agent was emitting) — regenerate as Cloud Access Policy tokens.

- Prometheus remote_write: `https://prometheus-prod-13-prod-us-east-0.grafana.net/api/prom/push`, user **`1179042`**
- Loki push: `https://logs-prod-006.grafana.net/loki/api/v1/push`, user **`688596`**
- Labels in use: `instance="pi-jayson"`, `job="integrations/raspberrypi-node"` (node/logs),
  `job="integrations/agent-check"` (self-metrics). Global `scrape_interval: 60s`.

## Reference: the actual old config (token REDACTED)

The real `/etc/grafana-agent.yaml` that Alloy must replicate (also kept at `~/grafana-agent.yaml.bak`
on the Pi). This is the authoritative translation source — note it has **no turret parsing**:

```yaml
integrations:
  prometheus_remote_write:
  - basic_auth:
      password: <REDACTED>
      username: 1179042
    url: https://prometheus-prod-13-prod-us-east-0.grafana.net/api/prom/push
  agent:
    enabled: true
    relabel_configs:
    - action: replace
      source_labels: [agent_hostname]
      target_label: instance
    - action: replace
      target_label: job
      replacement: "integrations/agent-check"
    metric_relabel_configs:
    - action: keep
      regex: (prometheus_target_.*|prometheus_sd_discovered_targets|agent_build.*|agent_wal_samples_appended_total|process_start_time_seconds)
      source_labels: [__name__]
  node_exporter:
    enabled: true
    relabel_configs:
    - replacement: 'pi-jayson'
      target_label: instance
    - replacement: integrations/raspberrypi-node
      target_label: job
logs:
  configs:
  - clients:
    - basic_auth:
        password: <REDACTED>
        username: 688596
      url: https://logs-prod-006.grafana.net/loki/api/v1/push
    name: integrations
    positions:
      filename: /tmp/positions.yaml
    scrape_configs:
    - job_name: integrations/node_exporter_journal_scrape
      journal:
        max_age: 24h
        labels: {instance: 'pi-jayson', job: integrations/raspberrypi-node}
      relabel_configs:
      - {source_labels: ['__journal__systemd_unit'], target_label: 'unit'}
      - {source_labels: ['__journal__boot_id'], target_label: 'boot_id'}
      - {source_labels: ['__journal__transport'], target_label: 'transport'}
      - {source_labels: ['__journal_priority_keyword'], target_label: 'level'}
    - job_name: integrations/node_exporter_direct_scrape
      static_configs:
      - targets: [localhost]
        labels:
          instance: 'pi-jayson'
          __path__: /var/log/{syslog,messages,*.log}
          job: integrations/raspberrypi-node
metrics:
  configs:
  - name: integrations
    remote_write:
    - basic_auth: {password: <REDACTED>, username: 1179042}
      url: https://prometheus-prod-13-prod-us-east-0.grafana.net/api/prom/push
  global:
    scrape_interval: 60s
  wal_directory: /tmp/grafana-agent-wal
```

Env (`/etc/default/grafana-agent`, also backed up): `CONFIG_FILE=/etc/grafana-agent.yaml`,
`CUSTOM_ARGS="-server.http.address=127.0.0.1:9090 -server.grpc.address=127.0.0.1:9091"`.

## Rollback (if ever needed)

The agent is intentionally gone and should stay gone. To restore the orphaned libs:
`sudo apt-get install cmake-data libfuse2 libjsoncpp24 librhash0`. The old config survives at
`~/grafana-agent.yaml.bak` / `~/grafana-agent.default.bak`. Reinstalling the agent itself is **not**
recommended — Grafana Agent is EOL; proceed to Alloy.
