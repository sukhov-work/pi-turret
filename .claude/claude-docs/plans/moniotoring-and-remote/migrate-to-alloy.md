# Migrating a Raspberry Pi 4 Monitoring Setup from Grafana Agent (Static) to Grafana Alloy → Grafana Cloud Free Tier (June 2026)

## TL;DR
- Install **Grafana Alloy v1.17.0** (released 2026-06-11) from the Grafana apt repo on Bullseye aarch64, regenerate Grafana Cloud credentials as **Cloud Access Policy tokens**, and deploy the complete `config.alloy` below — it faithfully replicates the dead Agent static config (node_exporter, journald, file scrape, self-metrics, remote_write/Loki).
- Grafana Cloud **Free tier (June 2026) = 10,000 active metric series, 50 GB logs/mo, 14‑day retention, 3 users** — plenty for one Pi, but log-derived metrics labeled with per-target IDs (`#53`) will explode cardinality, so keep those out of labels.
- Use `loki.process` `stage.regex` + `stage.metrics` to turn turret FIRE/state/aim-error log lines into Prometheus counters/gauges while keeping the raw logs queryable; monitor the two turret processes via node_exporter's systemd collector (run the app as a systemd service); build two dashboard groups (Pi system health via dashboard ID **1860** / Raspberry Pi integration, and turret telemetry).

## Key Findings

### The migration path is officially supported and low-risk
Grafana's own docs state verbatim: *"Grafana Agent has reached End-of-Life (EOL) on November 1, 2025. Agent is no longer receiving vendor support and will no longer receive security or bug fixes. Current users of Agent Static mode, Agent Flow mode, and Agent Operator should proceed with migrating to Grafana Alloy."* Alloy is a distribution of the OpenTelemetry Collector (v1.17.x bundles OTel Collector / Contrib v0.147.0) that embeds the same `node_exporter`, journald and file log collectors as the old Agent, so the migration is a near 1:1 component translation. Alloy ships a built-in converter: `alloy convert --source-format=static --output=config.alloy old-agent.yaml` mechanically translates your old static YAML. Because your config matches the official Grafana Cloud "Raspberry Pi" integration almost exactly, I have hand-written a clean `config.alloy` below rather than relying on the converter's verbose auto-generated component names.

### Current versions (June 2026)
- **Grafana Alloy: v1.17.0**, released 2026-06-11 (latest stable; supersedes v1.16.3 of 2026-06-05; the v1.17.0-rc.1 tag is a pre-release "for testing purposes only"). Installed via `apt install alloy` from `apt.grafana.com stable main`. arm64/aarch64 builds are published for every release (e.g. `alloy-linux-arm64`, assets dated 2026-06-12).
- Alloy config language: the HCL-like "Alloy syntax" (formerly River). Comments use `//`, block entries are comma-separated.
- The APT package creates an `alloy` system user, a systemd unit, and the default config at `/etc/alloy/config.alloy`; service settings live in `/etc/default/alloy`.

### Grafana Cloud Free tier (verified June 2026, grafana.com/pricing)
- **10,000 active series**, **50 GB of each telemetry type** (logs, traces, profiles), **500 VUh of k6**, **three users**, **14‑day retention**, no credit card. Auth is via **Cloud Access Policies + tokens** (the modern replacement for legacy API keys). For reference, Pro is a flat **$19/mo platform fee** plus usage ($6.50 per 1,000 active series/mo, $8 per active user/mo) — the trigger if you ever outgrow free.

## Details

### 1. Install Alloy on Debian 11 Bullseye (aarch64)

```bash
# Prereqs
sudo apt-get install -y gpg wget apt-transport-https

# Grafana APT repo (current 2026 key path)
sudo mkdir -p /etc/apt/keyrings/
wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list

sudo apt-get update -y
sudo apt-get install -y alloy        # installs v1.17.0 as of 2026-06-11
alloy --version                       # verify

# Remove the dead agent if still present
sudo systemctl disable --now grafana-agent 2>/dev/null || true
sudo apt-get remove -y grafana-agent 2>/dev/null || true
```

**Bullseye / Python 3.9 / systemd constraints:** Alloy is a static Go binary with no Python dependency, so the Pi's Python 3.9 is irrelevant to Alloy. Debian 11 ships systemd v247, which is fine for `loki.source.journal` (reads via libsystemd) and for node_exporter's systemd collector. One caveat: node_exporter's `service_restart_total` / `unit_start_time_seconds` metrics require newer systemd; on systemd 247 the basic `node_systemd_unit_state` works, but restart-count metrics may be unavailable (see process-liveness section). Also: the `alloy` user must be in the `systemd-journal` group and have read access to `/var/log/*` — add it with `sudo usermod -aG systemd-journal,adm alloy`.

Optionally enable the local debug UI by editing `/etc/default/alloy`:
```
CONFIG_FILE="/etc/alloy/config.alloy"
CUSTOM_ARGS="--server.http.listen-addr=127.0.0.1:12345"
```

### 2. Regenerate Grafana Cloud credentials (your old creds expired)

Your old usernames (Prometheus `1179042`, Loki `688596`) are the **instance/tenant IDs** — those don't change. What expired is the password (token). Generate a fresh Cloud Access Policy token:

1. Sign in at grafana.com → your Org → **Security → Access Policies** (or per-stack: Grafana → Administration → Cloud access policies).
2. **Create access policy.** Scopes: `metrics:write` and `logs:write` (add `metrics:read`/`logs:read` only if you'll query via the same token). Realm: your stack.
3. Select the policy → **Add token** → name it (e.g. `pi-jayson-alloy`), set an expiry, **Create**, and copy the token **immediately** (shown once).
4. Find the correct push URLs and usernames: in the Cloud Portal, open your stack → **Prometheus** "Details / Send Metrics" panel shows the remote_write URL and the numeric username; the **Loki** "Send Logs" panel shows the Loki push URL and its numeric username.

Your existing endpoints (keep these — they're correct for your account):
- Prometheus remote_write: `https://prometheus-prod-13-prod-us-east-0.grafana.net/api/prom/push`, username `1179042`
- Loki push: `https://logs-prod-006.grafana.net/loki/api/v1/push`, username `688596`

**Store the token out of the config file.** The cleanest approach for a single Pi is an EnvironmentFile. Create `/etc/alloy/secrets.env` (root-owned, `chmod 600`):
```
GRAFANA_CLOUD_PROM_USER=1179042
GRAFANA_CLOUD_LOKI_USER=688596
GRAFANA_CLOUD_TOKEN=glc_xxxxxxxxxxxxxxxxxxxxxxxx
```
Then create `/etc/systemd/system/alloy.service.d/override.conf`:
```
[Service]
EnvironmentFile=/etc/alloy/secrets.env
```
`sudo systemctl daemon-reload`. Reference them in config via `sys.env("GRAFANA_CLOUD_TOKEN")`. (Alternative: a `local.file` component reading a `chmod 600` key file — Alloy hot-reloads when the file changes.)

### 3. Complete, copy-paste `config.alloy`

Place at `/etc/alloy/config.alloy`.

```alloy
// ===================== /etc/alloy/config.alloy =====================
logging {
  level  = "info"
  format = "logfmt"
}

// --------- Credentials come from /etc/alloy/secrets.env ---------
// GRAFANA_CLOUD_PROM_USER / GRAFANA_CLOUD_LOKI_USER / GRAFANA_CLOUD_TOKEN

// =================== METRICS: remote_write sink ===================
prometheus.remote_write "grafana_cloud" {
  endpoint {
    url = "https://prometheus-prod-13-prod-us-east-0.grafana.net/api/prom/push"
    basic_auth {
      username = sys.env("GRAFANA_CLOUD_PROM_USER")
      password = sys.env("GRAFANA_CLOUD_TOKEN")
    }
  }
}

// =================== LOGS: Loki write sink =======================
loki.write "grafana_cloud_loki" {
  endpoint {
    url = "https://logs-prod-006.grafana.net/loki/api/v1/push"
    basic_auth {
      username = sys.env("GRAFANA_CLOUD_LOKI_USER")
      password = sys.env("GRAFANA_CLOUD_TOKEN")
    }
  }
}

// =================== node_exporter (unix) ========================
prometheus.exporter.unix "integrations_node_exporter" {
  // Enable systemd collector so we get node_systemd_unit_state for the
  // turret services (process liveness). Restrict units to keep cardinality low.
  enable_collectors = ["systemd"]
  systemd {
    unit_include = "(turret|turret-daemon|alloy)\\.service"
  }
}

discovery.relabel "integrations_node_exporter" {
  targets = prometheus.exporter.unix.integrations_node_exporter.targets
  rule {
    target_label = "instance"
    replacement  = "pi-jayson"
  }
  rule {
    target_label = "job"
    replacement  = "integrations/raspberrypi-node"
  }
}

prometheus.scrape "integrations_node_exporter" {
  targets         = discovery.relabel.integrations_node_exporter.output
  forward_to      = [prometheus.remote_write.grafana_cloud.receiver]
  job_name        = "integrations/node_exporter"
  scrape_interval = "60s"
}

// =================== Alloy self-metrics ==========================
// Replaces the old Agent "agent" self-metrics integration.
// NOTE: agent_* metrics are renamed alloy_* in Alloy.
prometheus.exporter.self "integrations_alloy_check" { }

discovery.relabel "integrations_alloy_check" {
  targets = prometheus.exporter.self.integrations_alloy_check.targets
  rule {
    target_label = "instance"
    replacement  = "pi-jayson"
  }
  rule {
    target_label = "job"
    replacement  = "integrations/agent-check"
  }
}

prometheus.scrape "integrations_alloy_check" {
  targets         = discovery.relabel.integrations_alloy_check.output
  forward_to      = [prometheus.relabel.alloy_check_filter.receiver]
  job_name        = "integrations/agent-check"
  scrape_interval = "60s"
}

// Keep-list to mirror your old metric_relabel keep-list (now alloy_*).
prometheus.relabel "alloy_check_filter" {
  forward_to = [prometheus.remote_write.grafana_cloud.receiver]
  rule {
    source_labels = ["__name__"]
    regex  = "(prometheus_target_.*|alloy_build.*|alloy_wal_samples_appended_total|process_start_time_seconds)"
    action = "keep"
  }
}

// =================== Logs: systemd journal =======================
discovery.relabel "journal" {
  targets = []
  rule {
    source_labels = ["__journal__systemd_unit"]
    target_label  = "unit"
  }
  rule {
    source_labels = ["__journal__boot_id"]
    target_label  = "boot_id"
  }
  rule {
    source_labels = ["__journal__transport"]
    target_label  = "transport"
  }
  rule {
    source_labels = ["__journal_priority_keyword"]
    target_label  = "level"
  }
}

loki.source.journal "journal" {
  max_age       = "24h0m0s"
  relabel_rules = discovery.relabel.journal.rules
  // Route through loki.process so we can extract turret metrics from
  // the turret service's journald output.
  forward_to    = [loki.process.turret.receiver]
  labels        = {
    instance = "pi-jayson",
    job      = "integrations/raspberrypi-node",
  }
}

// =================== Logs: direct file scrape ====================
local.file_match "varlogs" {
  path_targets = [{
    __address__ = "localhost",
    __path__    = "/var/log/{syslog,messages,*.log}",
    instance    = "pi-jayson",
    job         = "integrations/raspberrypi-node",
  }]
}

loki.source.file "varlogs" {
  targets    = local.file_match.varlogs.targets
  forward_to = [loki.write.grafana_cloud_loki.receiver]
}

// =================== Log-derived turret metrics ==================
// See section 5 below for the full stage breakdown.
loki.process "turret" {
  forward_to = [loki.write.grafana_cloud_loki.receiver]   // keep raw logs queryable

  // --- FIRE events: "FIRE #53 (shot 1, aim_err=7px)" ---
  stage.regex {
    expression = "FIRE #(?P<target_id>\\d+) \\(shot (?P<shot>\\d+), aim_err=(?P<aim_err>\\d+)px\\)"
  }
  stage.metrics {
    metric.counter {
      name        = "turret_fire_events_total"
      description = "Total turret fire events"
      source      = "aim_err"     // present only on FIRE lines
      action      = "inc"
      max_idle_duration = "24h"
    }
    metric.gauge {
      name        = "turret_aim_error_px"
      description = "Last aim error in pixels"
      source      = "aim_err"
      action      = "set"
      max_idle_duration = "24h"
    }
    metric.histogram {
      name        = "turret_aim_error_px_hist"
      description = "Aim error distribution (pixels)"
      source      = "aim_err"
      buckets     = [1, 2, 5, 10, 20, 50, 100]
    }
  }

  // --- State transitions: "state aiming -> firing" ---
  stage.regex {
    expression = "state (?P<from_state>\\w+) -> (?P<to_state>\\w+)"
  }
  stage.metrics {
    metric.counter {
      name        = "turret_state_transitions_total"
      description = "State machine transitions"
      source      = "to_state"
      action      = "inc"
      max_idle_duration = "24h"
    }
  }

  // --- Target lifecycle: acquired / lost / switch ---
  stage.regex {
    expression = "target (?P<event>acquired|lost|switch)"
  }
  stage.metrics {
    metric.counter {
      name        = "turret_target_events_total"
      description = "Target acquired/lost/switch events"
      source      = "event"
      action      = "inc"
      max_idle_duration = "24h"
    }
  }
}
```

After editing: `alloy validate /etc/alloy/config.alloy`, then `sudo systemctl restart alloy && sudo systemctl enable alloy`. Check `journalctl -u alloy -f` and the UI at `http://127.0.0.1:12345`.

**Key translation notes vs your old config:**
- `wal_directory: /tmp` → Alloy's WAL/storage path defaults to `/var/lib/alloy/data` (set by the package). Leave it there; `/tmp` is lost on reboot and not recommended. If you must, pass `--storage.path=/tmp/alloy` in CUSTOM_ARGS.
- `scrape_interval: 60s` global → set per `prometheus.scrape` (`scrape_interval = "60s"`).
- The old `agent` self-metrics keep-list referenced `agent_build*` / `agent_wal_samples_appended_total`; in Alloy these are renamed `alloy_build*` / `alloy_wal_samples_appended_total` — Grafana documents the blanket rule: *"Debug metrics reported by Alloy are prefixed with `alloy_` instead of `agent_`."* The keep-list above is updated accordingly.
- `instance: pi-jayson` is hard-coded as a relabel replacement (your old config used a fixed instance label, not the hostname).

### 4. Process / service liveness monitoring

**Run the turret app as a systemd service.** The repo's README currently launches it by hand (`python3 main.py`, a Bottle WSGI server on port 8001), which makes liveness monitoring impossible. Create `/etc/systemd/system/turret.service`:

```ini
[Unit]
Description=Pi Turret main app
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/pi-turret
ExecStart=/usr/bin/python3 /home/pi/pi-turret/main.py
Restart=on-failure
RestartSec=5
User=pi

[Install]
WantedBy=multi-user.target
```

The future GPIO/IR daemon becomes a second unit, e.g. `turret-daemon.service`. **Recommended pattern for the start/stop-by-daemon design:** have the IR daemon call `systemctl start turret.service` / `systemctl stop turret.service` (via a polkit rule or running it as root) rather than fork/managing a child process directly. This keeps both processes as first-class systemd units you can independently monitor, and `Restart=on-failure` gives automatic crash recovery without masking deliberate stops. (If the daemon manages a child process instead, only the daemon is visible to systemd and you'd be forced into `prometheus.exporter.process` for the child — more work and lower fidelity.)

**Recommended monitoring approach — node_exporter systemd collector (lowest overhead):** Since Alloy already bundles node_exporter, enabling the `systemd` collector with a tight `unit_include` regex (as in the config above) gives you, with zero extra processes:
- `node_systemd_unit_state{name="turret.service",state="active"}` → liveness (`=1` for active). Alert on `node_systemd_unit_state{name="turret.service",state="active"} == 0`.
- `node_systemd_unit_start_time_seconds` → detect restarts via `changes()` (where supported by the systemd version).

**For per-process CPU/memory**, add `prometheus.exporter.process` (the embedded process-exporter). Match by command line so you don't depend on PID:

```alloy
prometheus.exporter.process "turret_procs" {
  matcher {
    name    = "turret_main"
    cmdline = ["main\\.py"]
  }
  matcher {
    name    = "turret_daemon"
    cmdline = ["ir_daemon\\.py"]   // adjust to the future daemon's script name
  }
}
prometheus.scrape "turret_procs" {
  targets         = prometheus.exporter.process.turret_procs.targets
  forward_to      = [prometheus.remote_write.grafana_cloud.receiver]
  scrape_interval = "60s"
}
```

This yields `namedprocess_namegroup_num_procs{groupname="turret_main"}` (liveness: `=1` running, `=0`/absent down), `namedprocess_namegroup_cpu_seconds_total`, and `namedprocess_namegroup_memory_bytes{memtype="resident"}`. **Critical:** never put PID or `.StartTime` in the group name — Grafana's own docs warn this "is likely to result in high cardinality metrics." Use fixed `name` values as above. (Avoid the standalone systemd_exporter / process-exporter binaries; the embedded exporters cover this with no extra services.)

**Recommendation:** Use the node_exporter systemd collector for liveness/restarts (it's free — already running) and add `prometheus.exporter.process` only if you specifically want per-process CPU/mem trends. Two named matchers cover both the main app and the future IR daemon.

### 5. Log-derived metrics vs LogQL — the tradeoff

The `loki.process` block extracts metrics **at the agent**, before logs are shipped. The alternative is to keep logs as-is and compute everything at **query time** with LogQL (e.g. `sum(count_over_time({job="integrations/raspberrypi-node"} |= "FIRE" [5m]))`).

- **Agent-side `stage.metrics` (recommended for "rate over time / totals" panels):** produces real Prometheus counters/gauges/histograms that are cheap to query, alert on, and retain for 14 days as metrics. Cost: a few extra active series (well within free tier) and the metric resets if Alloy restarts (counters are designed for `rate()`/`increase()`, which tolerate resets).
- **LogQL at query time (recommended for ad-hoc exploration and the "show me the actual FIRE lines" panels):** zero extra series, full flexibility, but every dashboard refresh re-scans logs (counts against the 50 GB and can be slow over long windows).

Use both: `stage.metrics` for numeric trend panels (fire rate, aim-error distribution), LogQL for log-table panels.

**Exact regexes for your log lines** (Go RE2, as used in the config):
- FIRE: `FIRE #(?P<target_id>\d+) \(shot (?P<shot>\d+), aim_err=(?P<aim_err>\d+)px\)`
- State: `state (?P<from_state>\w+) -> (?P<to_state>\w+)`
- Target lifecycle: `target (?P<event>acquired|lost|switch)`

**CARDINALITY WARNING:** Do **not** promote `target_id` (the `#53`) to a metric label or a Loki label. Each unique target id would create a new series/stream; over weeks of operation this is unbounded — the fastest way to blow the 10k series cap. Keep `target_id` only inside the log line (queryable via LogQL when needed). In `stage.metrics`, only `to_state`/`from_state`/`event` are safe labels (small, finite sets). The config deliberately omits a `stage.labels` for `target_id`.

### 6. Dashboards

#### GROUP A — Raspberry Pi system health
**Import a maintained dashboard first.** Two good options:
- **Node Exporter Full — grafana.com dashboard ID `1860`** (author rfmoz; the most-downloaded node_exporter dashboard, "Nearly all default values exported by Prometheus node exporter"). Its page notes it recommends running node_exporter with `--collector.systemd --collector.processes` because some panels use those metrics. Import: Dashboards → New → Import → enter `1860` → select your Grafana Cloud Prometheus data source.
- **Grafana Cloud "Raspberry Pi" integration** (Connections → Raspberry Pi → Install) — adds the purpose-built "Raspberry Pi / overview" and "Raspberry Pi / logs" dashboards plus 15 alerts, all keyed to `job="integrations/raspberrypi-node"`. This is the exact match for your setup and is the **recommended choice**.

Key panels (PromQL) to confirm/build:
- **CPU usage per core:** `100 - (avg by (cpu) (rate(node_cpu_seconds_total{instance="pi-jayson",mode="idle"}[5m])) * 100)`
- **Load average:** `node_load1{instance="pi-jayson"}`, `node_load5`, `node_load15`
- **CPU temperature:** `node_hwmon_temp_celsius{instance="pi-jayson"}` (the Pi's SoC temp via node_exporter's hwmon collector on Pi OS)
- **RAM:** `node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes`; **Swap:** `node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes`
- **Disk usage:** `100 - (node_filesystem_avail_bytes{mountpoint="/"} * 100 / node_filesystem_size_bytes{mountpoint="/"})`
- **SD-card I/O:** `rate(node_disk_read_bytes_total[5m])`, `rate(node_disk_written_bytes_total[5m])`
- **Network throughput:** `rate(node_network_receive_bytes_total{device="eth0"}[5m])`, `..._transmit_bytes_total`
- **Uptime:** `node_time_seconds - node_boot_time_seconds`
- **systemd failed units:** `count(node_systemd_unit_state{state="failed"} == 1)`
- **Throttling / undervoltage:** node_exporter does **not** expose `vcgencmd get_throttled` natively. To get undervoltage/throttle bits, add a textfile-collector script that writes `vcgencmd get_throttled` output to a `.prom` file (community patterns: `fahlke/raspberrypi_exporter`, `darkhelmet46/vcgencmd-exporter`, `cavaliercoder/rpi_export`), or accept temperature (`node_hwmon_temp_celsius`) as a throttle proxy. Optional for a personal setup.

#### GROUP B — Turret / turret processes
A second dashboard (or a clearly separated row group) using the metrics from §4–5 plus Loki log panels:

Process / liveness (PromQL):
- **Main app up/down (Stat):** `node_systemd_unit_state{name="turret.service",state="active"}` (or `namedprocess_namegroup_num_procs{groupname="turret_main"}`)
- **IR daemon up/down (Stat):** `node_systemd_unit_state{name="turret-daemon.service",state="active"}`
- **Restarts (Stat/Timeseries):** `changes(node_systemd_unit_start_time_seconds{name="turret.service"}[1h])`
- **CPU per process (Timeseries):** `rate(namedprocess_namegroup_cpu_seconds_total{groupname=~"turret_.*"}[5m])`
- **Mem per process (Timeseries):** `namedprocess_namegroup_memory_bytes{groupname=~"turret_.*",memtype="resident"}`

Turret telemetry (PromQL from log-derived metrics):
- **Fire rate (Timeseries):** `sum(rate(turret_fire_events_total[5m]))`
- **Total shots (Stat):** `sum(increase(turret_fire_events_total[$__range]))`
- **Aim error trend (Timeseries):** `turret_aim_error_px`
- **Aim error distribution (Heatmap):** `sum(rate(turret_aim_error_px_hist_bucket[5m])) by (le)`
- **Target events rate (Timeseries):** `sum by (event) (rate(turret_target_events_total[5m]))`
- **State activity (Stat/Timeseries):** `sum by (to_state) (increase(turret_state_transitions_total[5m]))`

Log panels (LogQL — reconstructs the "aggregate/display specific logs by regex" board you lost). Data source = your Grafana Cloud Loki:
- **Only FIRE events (Logs panel):**
  `{job="integrations/raspberrypi-node", instance="pi-jayson"} |= "FIRE"`
  Parsed into a table:
  `{job="integrations/raspberrypi-node"} |= "FIRE" | pattern "FIRE #<target_id> (shot <shot>, aim_err=<aim_err>px)" | line_format "tgt={{.target_id}} shot={{.shot}} err={{.aim_err}}px"`
- **State transitions (Logs panel):**
  `{job="integrations/raspberrypi-node"} |~ "state \\w+ -> \\w+"`
- **Target lifecycle:**
  `{job="integrations/raspberrypi-node"} |~ "target (acquired|lost|switch)"`
- **Errors/warnings (Logs panel):**
  `{job="integrations/raspberrypi-node"} | level=~"error|warning"` (uses the `level` label from the journald relabel), or for file logs `{job="integrations/raspberrypi-node"} |~ "(?i)(error|warn|traceback)"`
- **FIRE count over time (Timeseries via LogQL, the no-extra-series alternative):**
  `sum(count_over_time({job="integrations/raspberrypi-node"} |= "FIRE" [5m]))`

Note: the Python app logs in `INFO:app.pipeline:...` stdlib-logging format. If run as a systemd service, its stdout/stderr go to the journal (captured by `loki.source.journal`, tagged `unit="turret.service"`) — tighten the log panels with `{unit="turret.service"}`. If it instead writes to a file under `/var/log`, the `loki.source.file` glob picks it up.

### 7. Keeping within free-tier limits & version control
- **Scrape interval 60s** (as configured) keeps data-points-per-minute low. Don't go below 60s for a personal Pi.
- **Cardinality discipline:** never label metrics with `target_id`/PID/boot-by-boot values. The journald `boot_id` label is bounded (one per reboot) but still accumulates streams over months — drop that relabel rule if you approach limits.
- **Filter metrics:** the `prometheus.relabel` keep-list on self-metrics already trims series. If you approach 10k series, add a keep-list on node_exporter too (the Raspberry Pi integration's "Filter Metrics" snippet drops everything not used by its dashboards).
- **Version-control the config:** keep `config.alloy` in the `pi-turret` repo (e.g. `monitoring/config.alloy`), but **never commit the token** — keep `secrets.env` out of git (`.gitignore`) and templated as `secrets.env.example`. Deploy with a small `make deploy` that copies the file to `/etc/alloy/`, runs `alloy validate`, then `systemctl reload alloy`.

## Recommendations
1. **Now:** Install Alloy v1.17.0, generate a new Cloud Access Policy token (`metrics:write`+`logs:write`), drop in the `config.alloy` above with `secrets.env`, `alloy validate`, start the service. Confirm data in Grafana Cloud Explore (`up{instance="pi-jayson"}` and `{instance="pi-jayson"}` logs).
2. **Next:** Convert the turret app to `turret.service`; the node_exporter systemd collector is already enabled in config. Import dashboard 1860 and/or install the Raspberry Pi integration. Verify `node_systemd_unit_state{name="turret.service"}`.
3. **Then:** Confirm the log-derived metrics appear (`turret_fire_events_total`, etc.), build the Group B dashboard. Add `prometheus.exporter.process` only if you want per-process CPU/mem.
4. **Benchmarks that change the plan:** If active series approach ~8k (check `grafanacloud_instance_metrics_active_series`), add node_exporter keep-lists and drop the histogram. If logs approach ~40 GB/mo, cut `loki.source.file` globs to just the turret log and rely on journald. If you ever need >14-day history, that's the trigger to consider Pro ($19/mo + usage).

## Caveats
- **Alloy apt version:** v1.17.0 (2026-06-11) is the confirmed latest GitHub stable; the apt `stable main` channel mirrors GitHub tags, so `apt install alloy` should give 1.17.0 — verify with `alloy --version` and pin if needed. (The raw apt Packages index was not separately read.)
- **Metric rename:** `agent_build_info`→`alloy_build_info` and `agent_wal_samples_appended_total`→`alloy_wal_samples_appended_total` are derived from Grafana's documented blanket rule (Alloy prefixes its debug metrics `alloy_` instead of `agent_`); there is no per-metric lookup table, so confirm exact names at `http://127.0.0.1:12345/metrics`.
- **systemd 247 on Bullseye:** `service_restart_total` / `unit_start_time_seconds` may be unavailable or partial; liveness via `node_systemd_unit_state` works regardless. Use `changes(node_systemd_unit_start_time_seconds[...])` only after confirming the metric exists.
- **The repo's exact log format is assumed.** The README shows a hand-run Bottle server and describes the project as a "laser turret" (the task frames it as a water-cannon bird-deterrent — cosmetic, doesn't affect monitoring). The FIRE/state/aim_err log strings in the task are taken as given; confirm the actual log strings on the Pi and adjust the regexes/patterns accordingly. The repo is mostly C (mjpg-streamer/edgetpu-yolo); the Python entry point is `main.py`.
- **vcgencmd throttling** is not in node_exporter; it needs a textfile-collector script if you want explicit undervoltage/throttle bits.
- **`alloy convert`** can auto-translate your old YAML, but test the output — bypassed/unsupported features emit warnings and converted behavior may not perfectly match static mode.