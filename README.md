# Biometric Bridge

Reads attendance punches from eSSL/ZK biometric terminals on a customer's LAN and
pushes them to that organisation's **existing, unmodified** HRMS API.

It runs as a Windows service with a small web portal for configuration and
monitoring. The HRMS API is never changed — the bridge adapts to it.

---

## What it does, and deliberately does not

**Does**
- Polls each configured terminal on a schedule, reads new attendance records, and
  pushes them to the HRMS attendance endpoint.
- Tracks a per-device watermark so nothing is missed and nothing is re-sent
  needlessly, surviving restarts, network outages and HRMS downtime.
- Runs unattended as a Windows service and starts on boot.

**Does not**
- **Store attendance data.** Punches exist in memory for the duration of a sync and
  are then discarded. SQLite holds configuration, sync state and per-run *counts* —
  nothing else. Enforced by a test, not just by intent.
- **Write to terminals.** No buffer clearing, no clock setting, no enrolment. The
  bridge is read-only against hardware.
- **Change the HRMS API.** Endpoint, method, auth, field names and response parsing
  are all configuration.

---

## Architecture

```
  eSSL terminal ──┐
   (TCP 4370)     │        ┌──────────────────────────┐
                  ├───LAN──▶  Biometric Bridge         │────HTTPS 443───▶ HRMS API
  eSSL terminal ──┘        │  • device connector       │                  (unchanged)
                           │  • sync engine            │
   Operator ───LAN:10255───▶  • scheduler              │
   (browser, HTTP)         │  • React portal + SQLite  │
                           └──────────────────────────┘
                              Windows service, one process
```

One process, one port, one SQLite file. No message broker, no container runtime, no
external cache — the workload does not justify any of it.

---

## Two facts that shape the whole design

**1. The ZK protocol has no server-side time filter.** `get_attendance()` transfers
the terminal's *entire* buffer and the caller filters in Python. Measured against the
reference devices: ~7.5 s to transfer ~20,000 records, per device, per poll. This is
why the default interval is 5 minutes and why a 1-minute interval carries a warning.

**2. Terminal clocks drift, and drift silently corrupts attendance.** Device
timestamps are naive local time. The bridge builds its sync window from the *device*
clock, records the offset, and **refuses to sync** if that clock moves backwards past
the watermark or jumps further than tolerated — both of which otherwise cause
invisible data loss behind a healthy-looking dashboard. It never corrects a device
clock; that is an operator action on the terminal.

---

## Getting started (operator)

1. Extract the package and run `install.ps1` as Administrator.
2. Open `http://<machine>:10255`.
3. **Create the administrator account.** The bridge ships with no credentials at
   all, so the first visit is a setup screen rather than a login form.
4. Add each terminal (IP, port, and whether it is an entry or exit device) and press
   **Test Connection**.
5. Enter the HRMS base URL, API key and endpoint profile — see
   [docs/HRMS-PROFILE.md](docs/HRMS-PROFILE.md).
6. Check the first-sync lookback in **Settings**, then enable automatic sync.

Full detail in [docs/INSTALLATION.md](docs/INSTALLATION.md).

---

## Development

Requires Python 3.12+ and, for the portal, Node 20+.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt

# Backend (portal optional — the API runs without a build)
.\.venv\Scripts\python -m uvicorn app.main:app --reload --app-dir backend

# Portal, with API proxying to the backend
cd frontend; npm install; npm run dev
```

```powershell
.\.venv\Scripts\python -m pytest              # tests
.\.venv\Scripts\python -m pytest --cov        # with coverage
.\.venv\Scripts\python -m ruff check backend  # lint
.\.venv\Scripts\python -m mypy backend/app    # types
```

Against real hardware, from `backend/`:

```powershell
..\.venv\Scripts\python tests\manual\live_device_check.py
```

Read-only. Reports firmware, buffer size, clock offset and the observed
`(status, punch)` pairs — the mapping that must be confirmed per firmware family.

### Layout

```
backend/app/
  biometric/   device abstraction, eSSL connector, record normalizer
  hrms/        profile model, payload mapper, HTTP client
  services/    sync engine, locking, auth, settings, backups
  scheduler/   APScheduler jobs
  api/routes/  FastAPI endpoints
  core/        config, logging, errors, enums
frontend/      React + Vite portal (builds into backend/app/static)
deployment/    build, install and operator scripts
docs/          installation, configuration, HRMS profile, troubleshooting, security
```

---

## Building a release

```powershell
.\deployment\build.ps1
```

Builds the portal, runs the tests, freezes the backend with PyInstaller, and stages
everything with WinSW into `dist/`. **Node and Python are needed on the build
machine only** — the target needs neither.

WinSW is not vendored: download `WinSW-x64.exe` from the
[WinSW releases](https://github.com/winsw/winsw/releases), rename it to `WinSW.exe`
and place it in `deployment/winsw/`.

---

## Documentation

| Document | Contents |
|---|---|
| [INSTALLATION.md](docs/INSTALLATION.md) | Install, upgrade, uninstall, firewall |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting and what it affects |
| [HRMS-PROFILE.md](docs/HRMS-PROFILE.md) | Wiring your HRMS API contract |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptoms, causes, fixes |
| [SECURITY.md](docs/SECURITY.md) | What is protected, what is not, and why |
| [PLAN.md](PLAN.md) | Design rationale and open questions |

---

## Current status

Backend, portal, scheduler and packaging are complete and tested. Two items are
outstanding and are recorded in [PLAN.md](PLAN.md):

- **The HRMS contract** — the profile shim is built and tested against a fake HRMS,
  but the real endpoint, field names and auth scheme still need supplying. Nothing
  about it is guessed or hardcoded.
- **Duplicate handling** — the overlap window and retry-after-timeout mean the
  attendance endpoint may occasionally receive the same punch twice. Whether that is
  harmless needs confirming; see the closing section of
  [HRMS-PROFILE.md](docs/HRMS-PROFILE.md).
