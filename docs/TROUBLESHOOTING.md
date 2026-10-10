# Troubleshooting

Start with `status.ps1` — it checks the service, the portal and recent errors in one
pass. Then the portal's **Logs & About** page, which shows the same log with a level
filter and needs no remote desktop session.

---

## Attendance has stopped appearing in HRMS

Work down this list; it is ordered by how often each turns out to be the cause.

**1. Is automatic sync on?** Dashboard → Scheduler. A fresh install has it off by
design, and the dashboard says so rather than showing a reassuring green.

**2. Is HRMS enabled and configured?** Dashboard → HRMS. If *Endpoint configured* is
"Not set", the profile still carries the shipped placeholder and the bridge will not
guess an endpoint.

**3. What does the last run say?** Sync History, newest first. The status names the
cause:

| Status | Meaning | Fix |
|---|---|---|
| `NO_RECORDS` | Ran fine, nothing new | Normal outside working hours |
| `DEVICE_UNAVAILABLE` | Terminal unreachable | Power, cable, IP, ping |
| `TIMEOUT` | Too slow to answer | Raise the device timeout; check the network |
| `DEVICE_CLOCK_REGRESSION` | Clock moved backwards past the watermark | See *Clock problems* |
| `DEVICE_CLOCK_JUMP` | Clock moved more than tolerated | See *Clock problems* |
| `HRMS_UNAVAILABLE` | HRMS down, 5xx or rate-limited | Usually resolves; the next cycle retries |
| `HRMS_AUTHENTICATION_FAILED` | Key rejected | Re-enter it in HRMS Configuration |
| `HRMS_BAD_REQUEST` | Payload does not match the contract | The profile needs correcting |
| `INVALID_RECORD` | Every record in the window failed validation | Check the log for the reason |
| `INTERRUPTED` | The service stopped mid-sync | Recovered automatically on the next run |

**4. Is the watermark stuck?** Sync page → per-device state. If *Last successful
push* has not moved but runs are succeeding, the device genuinely has no new records.

---

## Clock problems

**Symptom:** `DEVICE_CLOCK_REGRESSION` or `DEVICE_CLOCK_JUMP`, and sync stops for
that device.

**Why the bridge refuses rather than continuing.** A backwards clock inverts the sync
window, so every subsequent run returns nothing — for hours — while the dashboard
still looks fine. A forward jump drags the watermark with it and strands every record
in between. Both are silent. Stopping and saying so is the lesser harm.

**Fix**

1. Correct the time on the terminal itself. The bridge never sets a device clock.
2. Sync → **Historical range**, and cover the affected period for that device. This
   pushes what was missed without disturbing automatic sync.
3. The next scheduled cycle resumes normally.

**Persistent drift** (the dashboard shows a drift badge but sync still works): every
punch is being recorded with that offset. Both reference terminals were found ~150 s
slow. Worth fixing before it becomes a payroll dispute.

---

## The portal will not load

| Check | Command |
|---|---|
| Service running | `.\status.ps1` |
| Port listening | `Test-NetConnection localhost -Port 10255` |
| Firewall rule | `Get-NetFirewallRule -DisplayName 'Biometric Bridge Portal'` |
| From another machine | Confirm you are on the same subnet — the rule is LocalSubnet only |

If the service starts and immediately stops, read
`C:\ProgramData\BiometricBridge\logs\biometric_bridge.log`, and the WinSW log beside
it for failures that happen before logging is configured. The usual causes are a port
already in use and permissions on `ProgramData`.

---

## I cannot sign in

- **No account exists.** A fresh install shows a setup screen, not a login form.
  There are no default credentials.
- **Locked out.** Ten consecutive failures lock an account for 30 minutes. Wait, or
  have another administrator reset the password.
- **Rate limited (HTTP 429).** Five failures in 15 minutes from the same
  username and address. It clears itself.
- **Only administrator, password lost.** There is no backdoor by design. Stop the
  service, delete the `users` table row with any SQLite tool, restart, and the setup
  screen returns.

---

## "Synchronization is already running" (409)

Another sync holds that device's lock. Either a scheduled cycle is genuinely running,
or a previous process died mid-sync.

A lock left by a crash is reclaimed automatically after the stale-lock timeout
(default 30 minutes), and any lock owned by the current process is cleared at
startup. If it persists beyond that, restart the service.

---

## Duplicate attendance in HRMS

Expected in one narrow case: the overlap window and a retry-after-timeout can both
present the same punch twice.

- If your API deduplicates, nothing to do.
- If it does not, set **Overlap** to 0 in Settings. That removes the duplicate risk
  and accepts a small chance of missing a record when a device clock drifts.
- If your API has an idempotency or external-reference field, map it to
  `client_record_id` in the profile — that removes the risk entirely.

---

## Sync is slow

Every poll transfers the terminal's whole buffer; the protocol offers no way to ask
for "records since T". Sync History shows *Buffer* (whole buffer) separately from
*In window* for exactly this reason.

If *Buffer* is large and reads take many seconds, increase the interval. Clearing the
device buffer would help but the bridge will not do it — it is destructive and the
bridge is not the system of record.

---

## Reading the log directly

```powershell
Get-Content C:\ProgramData\BiometricBridge\logs\biometric_bridge.log -Tail 50 -Wait
Get-Content C:\ProgramData\BiometricBridge\logs\biometric_bridge.log |
    Select-String 'ERROR' | Select-Object -Last 20
```

One JSON object per line. Credentials are removed before anything is written, so the
log is safe to send to support as-is.
