# Grafana Agent / Obsolete Collector Cleanup — Instructions & Caveats

*Hand-off brief for an agent session. Goal: remove the dead `grafana-agent` and any standalone collectors Alloy will replace, **before** the Alloy migration. Not a script — discover first, then run only the steps that match what's present. Every removal is gated on actual presence so it's safe and idempotent.*

## Caveats (read first — these cause real breakage if missed)

1. **Do NOT remove the `apt.grafana.com` repo or its GPG key.** Alloy installs from the same repo in the migration step. Removing it breaks the very next thing you do. Dedupe duplicate repo lines if found, but keep one.
2. **Stop/disable the agent before Alloy is ever started.** Grafana Agent and Alloy **both default their HTTP server to port `12345`**. If the agent is still bound to it, Alloy fails (often silently) at startup. This is the main reason cleanup precedes migration.
3. **Distinguish package install vs binary-drop install** before removing. `apt-get purge` only works if the package is dpkg-known; a hand-dropped binary needs manual `rm` of the binary + unit files. Discovery Step 1 tells you which.
4. **`apt-get purge <pkg>` errors if the package is absent** ("Unable to locate package"), so gate every purge on a `dpkg -l` hit. Don't run purge blind.
5. **Back up `/etc/grafana-agent.yaml` before deleting it.** It's already translated into `config.alloy`, but keep the original as reference/rollback.
6. **Debian commonly leaves the `grafana-agent` system user/group behind on purge.** Remove them explicitly after the package is gone.
7. **Standalone collectors (node_exporter, promtail) are removed because Alloy embeds them** (`prometheus.exporter.unix`, `loki.source.*`). Only remove them if discovery finds them AND nothing else on the box depends on them. `telegraf`/`collectd`/`cadvisor` are separate tools — do not remove unless you know they're unused.
8. **Protect Alloy if a previous partial migration already installed it.** This cleanup must never stop, disable, or remove `alloy`. If discovery shows Alloy present, leave it untouched.
9. **The old credentials are already deactivated on Grafana's side**, so the agent isn't double-shipping right now. But a still-running dead agent spams push `401`s into journald — and the new Alloy `loki.source.journal` would ingest those into Loki and pollute the turret log panels. Removing it keeps both the port and the logs clean.
10. **Run removal as root** (`sudo`). Discovery is read-only and mostly works unprivileged (some `find` paths need root; redirect stderr).

---

## Step 1 — Discovery (read-only; decides what the later steps touch)

```bash
# Packages (Debian) — is the agent a package, and are standalone collectors present?
dpkg -l | grep -Ei 'grafana-agent|grafana-agent-flow|alloy' || echo "no grafana-agent/alloy packages"
dpkg -l | grep -Ei 'node-exporter|prometheus|promtail|telegraf|collectd|cadvisor' || echo "no standalone collectors"

# Services / units
systemctl list-unit-files | grep -Ei 'grafana-agent|alloy|node.?exporter|promtail|telegraf|collectd'

# Binaries (catches a non-apt binary-drop install)
which grafana-agent grafana-agentctl 2>/dev/null
ls -l /usr/bin/grafana-agent /usr/local/bin/grafana-agent 2>/dev/null

# Is the config package-owned or hand-placed? (determines removal method)
dpkg -S /etc/grafana-agent.yaml 2>/dev/null || echo "/etc/grafana-agent.yaml not owned by a package (hand-placed)"

# Files, data, user/group
sudo find /etc /var/lib /tmp -maxdepth 2 -iname '*grafana-agent*' 2>/dev/null
getent passwd grafana-agent; getent group grafana-agent

# Who holds :12345 (agent and Alloy both default here)
sudo ss -ltnp | grep 12345 || echo "nothing on :12345"

# apt repo entries + keys — you KEEP one of these for Alloy; only dedupe extras
grep -RIn 'apt.grafana.com' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null
ls -l /etc/apt/keyrings/grafana.gpg /etc/apt/trusted.gpg.d/grafana.gpg /usr/share/keyrings/grafana.gpg 2>/dev/null
```

Interpretation:
- `grafana-agent` in `dpkg -l` → use the **package** path (Step 3a).
- `dpkg -S` says "not owned" but a binary exists → use the **binary-drop** path (Step 3b).
- `alloy` present anywhere → do not touch it; skip any line that would.
- `:12345` held by `grafana-agent` → expected; Step 2 frees it. Held by `alloy` → migration already partly done; stop and reassess.

---

## Step 2 — Stop and disable the service

```bash
sudo systemctl disable --now grafana-agent 2>/dev/null || true
sudo systemctl disable --now grafana-agent-flow 2>/dev/null || true
```

---

## Step 3 — Remove the agent (pick ONE path from discovery)

### 3a. Package install (normal case: `grafana-agent` in `dpkg -l`)
```bash
sudo apt-get purge -y grafana-agent grafana-agent-flow
sudo apt-get autoremove -y
```

### 3b. Binary-drop install (`dpkg -S` said "not owned by a package")
```bash
sudo rm -f /etc/systemd/system/grafana-agent.service /lib/systemd/system/grafana-agent.service
sudo rm -f /usr/bin/grafana-agent /usr/local/bin/grafana-agent \
           /usr/bin/grafana-agentctl /usr/local/bin/grafana-agentctl
```

---

## Step 4 — Purge leftovers (config, env, data, user/group, failed state)

```bash
# Backup first (already translated into config.alloy; keep the original)
sudo cp /etc/grafana-agent.yaml ~/grafana-agent.yaml.bak 2>/dev/null || true

# Config + env (purge usually removes conffiles; clean any residue)
sudo rm -f /etc/grafana-agent.yaml /etc/default/grafana-agent

# Data dir
sudo rm -rf /var/lib/grafana-agent

# WAL + positions lived in /tmp per the old config (ephemeral, but clear anyway)
sudo rm -rf /tmp/grafana-agent-wal /tmp/positions.yaml

# Debian leaves the system user/group behind on purge
sudo userdel grafana-agent 2>/dev/null || true
sudo groupdel grafana-agent 2>/dev/null || true

# Clear failed-unit state and reload systemd
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

---

## Step 5 — Remove standalone collectors Alloy replaces (ONLY if found in Step 1, and unused elsewhere)

Alloy embeds node_exporter (`prometheus.exporter.unix`) and the Loki agent (`loki.source.*`), so these become redundant:
```bash
sudo apt-get purge -y prometheus-node-exporter   # built into Alloy now
sudo apt-get purge -y promtail                    # replaced by Alloy loki.* components
```
`telegraf` / `collectd` / `cadvisor` are **separate tools** — remove only if you confirmed they're present and nothing else uses them:
```bash
sudo apt-get purge -y telegraf collectd cadvisor  # discretionary; confirm unused first
```

---

## Step 6 — apt repo: keep one, dedupe extras

Keep `apt.grafana.com` and its key (Alloy needs them). If Step 1 showed the repo defined **twice** (e.g. an old line in `/etc/apt/sources.list` plus a newer `sources.list.d/grafana.list`, or both `trusted.gpg.d/grafana.gpg` and `keyrings/grafana.gpg`), delete the older duplicate and keep the keyring-signed `sources.list.d/grafana.list` entry. Then:
```bash
sudo apt-get update
```

---

## Step 7 — Verify clean

```bash
dpkg -l | grep -Ei 'grafana-agent' && echo "STILL PRESENT" || echo "grafana-agent fully removed"
systemctl status grafana-agent --no-pager 2>/dev/null || echo "no grafana-agent unit"
sudo find / -xdev -iname '*grafana-agent*' 2>/dev/null   # expect nothing (or only your .bak)
sudo ss -ltnp | grep 12345 || echo ":12345 free (good — Alloy can bind it)"
getent passwd grafana-agent || echo "user removed"
```

Expected end state: no `grafana-agent` package/unit/binary, `:12345` free, user/group gone, the `apt.grafana.com` repo still present (single entry), `~/grafana-agent.yaml.bak` retained. The box is now ready for the Alloy install in the migration brief.
