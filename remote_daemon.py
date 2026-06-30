#!/usr/bin/env python3
"""Entrypoint for the IR remote supervisor (turret-remote.service).

A separate always-on process that owns the IR receiver, runs ``systemctl start/stop``
on the turret unit for the POWER key, and forwards every other key to the running
app's web API on :8001. Logic + rationale live in ``app/remote_supervisor.py``.

Run on the Pi:  python3 remote_daemon.py   (normally via systemd; see
monitoring/systemd/turret-remote.service).
"""
from app.remote_supervisor import main

if __name__ == "__main__":
    main()
