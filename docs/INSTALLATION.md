# Installation

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11 / Server 2016+ (x64) |
| Privileges | Local Administrator, to register a service |
| Network | Same LAN as the terminals; outbound HTTPS to HRMS |
| Disk | ~250 MB program files, plus a small and slow-growing database |
| Runtime | **None.** Python and Node are needed on the build machine only |

The machine must stay powered on: attendance is collected by polling, so nothing is
gathered while it is off. Terminals buffer their own records, so a short outage is
recovered automatically — but only up to the first-sync lookback if the watermark is
also lost.

---

## Ports

| Direction | Port | Peer | Purpose |
|---|---|---|---|
| Inbound | TCP 8080 | LAN subnet only | The portal |
| Outbound | TCP 4370 | Terminals | ZK protocol |
| Outbound | TCP 443 | HRMS | Attendance push |

`install.ps1` adds the inbound rule scoped to **LocalSubnet**, not Any. That scoping
is doing real work: the portal runs over plain HTTP, so the session cookie and the
sign-in password are readable by anyone who can see the traffic. It must not be
reachable from beyond the local network.

Outbound rules are not created — Windows permits outbound by default.

---

## Install

1. Copy `BiometricBridge-package.zip` to the machine and extract it.
2. Open PowerShell **as Administrator** in the extracted folder.
3. Run:

```powershell
.\install.ps1
```

Options: `-Port 9090`, `-SkipFirewall`, `-InstallDir "D:\Apps\BiometricBridge"`.

The script creates the directories, locks down permissions on the data directory,
registers the service for automatic start, adds the firewall rule, starts the
service and waits for `/health` to answer. It prints the portal URL when done.

### After installing

1. Open `http://<machine-name>:8080`.
2. **Create the administrator account.** There are no default credentials, so the
   first visit is a setup screen. The minimum length is configurable
   (`BIOMETRIC_BRIDGE_MIN_PASSWORD_LENGTH`, default 4) — but this portal is plain
   HTTP on the LAN, so use a real password in production.
3. **Devices** → add each terminal. Set *Punch type* to match the hardware: a
   dedicated entry terminal is `CHECK_IN`, an exit terminal is `CHECK_OUT`. Use
   **Test Connection** before saving — it reports firmware, buffer size and clock
   offset.
4. **HRMS Configuration** → base URL, API key, and the profile describing your API.
   See [HRMS-PROFILE.md](HRMS-PROFILE.md).
5. **Settings** → check *First-sync lookback* before enabling automatic sync. It
   decides how far back a brand-new device reaches on its very first run. The
   default of 24 hours is a deliberate safeguard: a terminal holding two years of
   history would otherwise push all of it into HRMS at once.
6. Enable automatic sync and confirm the dashboard turns green.

---

## Directory layout

```
C:\Program Files\BiometricBridge\      program files, replaced on upgrade
C:\ProgramData\BiometricBridge\
    data\      biometric_bridge.db     configuration and sync state
    logs\      biometric_bridge.log    rotating JSON log
    config\                            reserved
    backups\                           database snapshots
```

`ProgramData` is restricted to SYSTEM and Administrators, with inheritance removed.
That matters because the database holds the HRMS API key in plain text — see
[SECURITY.md](SECURITY.md). **Backups contain it too**, so treat that folder with the
same care and do not copy it somewhere less protected.

---

## Upgrading

Re-run `install.ps1` from the new package. It stops the service, replaces the
program files and restarts.

`ProgramData` is never touched, so configuration and — importantly — the per-device
watermarks survive. The service applies any pending database migrations itself on
start; there is no separate migration step.

---

## Uninstalling

```powershell
.\uninstall.ps1                 # keeps configuration and sync state
.\uninstall.ps1 -RemoveData     # deletes them too, after confirmation
```

Keeping the data is the default on purpose. The watermarks record exactly what HRMS
has already received; losing them means a future install restarts from its lookback
window and may re-send attendance HRMS already has.

---

## Verifying an installation

```powershell
.\status.ps1
```

Reports service state, portal health, database size and recent errors. This is the
first thing to run when someone says attendance has stopped appearing.

To confirm the terminals are reachable and the field mapping is right, run the live
check from a development checkout on the same LAN:

```powershell
..\.venv\Scripts\python tests\manual\live_device_check.py
```

It is read-only and never writes to a device.

---

## Service management

```powershell
.\start.ps1 ; .\stop.ps1 ; .\restart.ps1 ; .\status.ps1
```

The service is registered as automatic-start and restarts itself on failure with a
widening delay (10 s, 60 s, 300 s). A terminal or HRMS being unreachable is a normal
condition and does not crash it — those show as failed sync runs, not as a stopped
service.

---

## Known operational notes

- **Terminal clocks.** Both reference devices were found running ~150 seconds slow.
  Every punch inherits that offset. The bridge reports drift but never corrects a
  device clock; fix it on the terminal before going live.
- **Poll cost.** Each cycle transfers each terminal's whole buffer, ~7.5 s for
  ~20,000 records. Two devices is ~15 s of work per cycle. Comfortable at 5 minutes;
  the portal warns if you choose an interval that leaves little headroom.
