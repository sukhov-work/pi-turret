# Next-Session Prompt — Install Grafana Alloy + build Grafana Cloud dashboards from scratch

*Paste the block below into the next `/turret` session. The Pi was cleaned of the dead Grafana Agent
on 2026-06-29 (see `grafana-agent-cleanup-report.md`) — it's a clean slate. The full design is in
`migrate-to-alloy.md`; this prompt carries the corrections that doc needs.*

---

## PROMPT

Implement the Pi monitoring stack from scratch: install **Grafana Alloy** on the Pi and stand up
**Grafana Cloud dashboards**, following `.claude/claude-docs/plans/moniotoring-and-remote/migrate-to-alloy.md`.
The old Grafana Agent was fully removed last session — the box is clean (no agent, no apt repo, no
collectors, ports free). Read `grafana-agent-cleanup-report.md` in the same folder first; it records
the verified Pi state and **six findings that correct the migrate doc**. Apply these corrections:

1. **Add the Grafana apt repo + GPG key — it's ABSENT, so this is required, not optional.** Run
   `migrate-to-alloy.md` §1 verbatim (the `apt.grafana.com` repo, key in `/etc/apt/keyrings/grafana.gpg`,
   `sources.list.d/grafana.list`), then `apt install alloy`. Verify `alloy --version`.
2. **Regenerate Grafana Cloud credentials.** The old tokens are revoked (the agent was emitting
   `401 invalid token`). Create a Cloud Access Policy token with `metrics:write`+`logs:write`. The
   tenant IDs are unchanged: Prometheus user **`1179042`** (`prometheus-prod-13-prod-us-east-0.grafana.net`),
   Loki user **`688596`** (`logs-prod-006.grafana.net`). Store the token in `/etc/alloy/secrets.env`
   (root, `chmod 600`) via the systemd `EnvironmentFile` override — **never commit it**.
3. **`config.alloy` must replicate the real old config**, which is quoted (redacted) in the cleanup
   report and saved on the Pi at `~/grafana-agent.yaml.bak`. Labels: `instance="pi-jayson"`,
   `job="integrations/raspberrypi-node"` (node + logs), `job="integrations/agent-check"` (self-metrics),
   `scrape_interval=60s`, journald relabels (unit/boot_id/transport/level), file scrape
   `/var/log/{syslog,messages,*.log}`. Keep `prometheus_sd_discovered_targets` in the self-metrics
   keep-list (the doc dropped it); confirm exact `alloy_*` metric names at `:12345/metrics`.
4. **The turret log-derived metrics (FIRE/state/aim_err) are GREENFIELD — verify the format first.**
   The old config did **no** turret parsing; the doc's regexes are assumed. Before writing the
   `loki.process` stages, check what the v2 app actually logs (`app/control.py`, `app/pipeline.py`,
   `app/statemachine.py` use stdlib logging) and adjust the regexes to the real strings, or the metrics
   stay empty. Honor the **cardinality warning**: never put `target_id`/PID/`boot_id` in a metric label.
5. **Run the turret app as a systemd service** (`migrate-to-alloy.md` §4) so node_exporter's systemd
   collector can watch liveness — BUT fix the unit: use `User=jayson` and
   `WorkingDirectory=/home/jayson/pi-turret` (the doc's `/home/pi` + `User=pi` are wrong for this box).
   Confirm the actual v2 launch command before writing `ExecStart` (it may not be a bare `main.py` at
   repo root — check the repo / venv). This is a real behavior change to how the turret starts — **get
   owner confirmation before enabling it.**
6. **Add the `alloy` user to `systemd-journal` + `adm`** (`usermod -aG systemd-journal,adm alloy`) — the
   old agent had these; without them journald/file logs won't read. `boot.log` may still be unreadable;
   exclude or ignore it.

Then: `alloy validate /etc/alloy/config.alloy` → `systemctl enable --now alloy` →
`journalctl -u alloy -f` and the UI at `http://127.0.0.1:12345`. Confirm data in Grafana Cloud Explore
(`up{instance="pi-jayson"}` and `{instance="pi-jayson"}` logs). Build the two dashboard groups from
§6: Group A = Raspberry Pi system health (install the Cloud **Raspberry Pi integration**, keyed to
`job="integrations/raspberrypi-node"`), Group B = turret telemetry + liveness + log panels.

**Constraints / hygiene:**
- Pi reach: `ssh pi` (host `pi-jayson`, user `jayson`, passwordless sudo, Bullseye aarch64). Long work →
  `mosh pi` + `tmux`. Deploy code via `git push pi main`, not rsync.
- Version-control `config.alloy` in the repo (e.g. `monitoring/config.alloy`) with a
  `secrets.env.example`; **`secrets.env` stays gitignored** — never commit the token.
- Mil-tech dashboard aesthetic (serious/minimal, not neon) per the owner's UI preference.
- This is monitoring infra (orthogonal to the detector/aim core) — don't touch v1, the v2 control
  code, or the model pipeline. Record the outcome in `DECISIONS.md` when done.

---

## Quick checklist for the next session

- [ ] §1 add apt repo + key (absent — mandatory) → `apt install alloy` → `alloy --version`
- [ ] §2 new Cloud Access Policy token (`metrics:write`+`logs:write`) → `/etc/alloy/secrets.env` (600) + systemd override
- [ ] §3 write `/etc/alloy/config.alloy` from `~/grafana-agent.yaml.bak` (keep `prometheus_sd_discovered_targets`)
- [ ] §4 verify real v2 turret log strings → write/adjust `loki.process` regexes (no high-cardinality labels)
- [ ] §4 `turret.service` with **`User=jayson` + `/home/jayson/pi-turret`** + correct ExecStart — **confirm with owner before enabling**
- [ ] `usermod -aG systemd-journal,adm alloy`
- [ ] `alloy validate` → `systemctl enable --now alloy` → confirm in Grafana Cloud Explore
- [ ] Dashboards: install Cloud Raspberry Pi integration (Group A) + build turret board (Group B)
- [ ] Commit `monitoring/config.alloy` + `secrets.env.example` (gitignore `secrets.env`); log in `DECISIONS.md`
