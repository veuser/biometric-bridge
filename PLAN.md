# Biometric Bridge — Implementation Plan

**Status:** Plan for review — no implementation code written yet.
**Location:** `d:\Dev\git-repos\Ve\biometric-bridge`
**Date:** 2026-08-08

---

## 0. Scope statement

A standalone LAN application that reads attendance punches from eSSL/ZK biometric
devices and pushes them to an **existing, unmodified** HRMS API.

**Out of scope, permanently:** any change to the HRMS application, its database, its
API contract, its auth. The bridge adapts to HRMS, never the reverse.

**Not stored locally:** biometric punch records, fingerprints, face templates,
device passwords, employee personal data. Punches exist only in memory during a
sync run. SQLite holds configuration, sync state, sync-run metadata, and portal users.

---

## 1. Findings from the existing spike

`biometric_test/test_biometric.py` is a working proof of connectivity against two
live devices (`172.20.200.26` = entry, `172.20.200.25` = exit, port 4370, pyzk 0.9,
Python 3.12). Three things found while reading it materially change the design.

### 1.1 Bug — verification type is read from the wrong field

The spike does:

```python
VERIFY_TYPE = {0: "Fingerprint", 1: "Password", 2: "Card"}
method = VERIFY_TYPE.get(a.punch, ...)
```

In the ZK record layout (`pyzk/base.py:1597`, unpacked as
`uid, user_id, status, timestamp, punch, space`), the two fields are:

| pyzk field | ZK meaning | Values |
|---|---|---|
| `attendance.status` | **verification mode** | 0 password, 1 fingerprint, 2 card, 15 face |
| `attendance.punch` | **punch state** | 0 check-in, 1 check-out, 2 break-out, 3 break-in, 4 OT-in, 5 OT-out |

So the spike reads the punch-state field and labels it as a verification method.
This is why your sample row reads `Check Out / Password / Check Out` — the middle
value is punch-state `1` mis-decoded as "Password". The bridge will read
`status` for verification mode and `punch` for punch state, and will surface the
raw integers in device diagnostics so the mapping can be verified against your
actual hardware before go-live.

**VERIFIED ON LIVE HARDWARE — 2026-08-08.** `tests/manual/live_device_check.py` was
run against both terminals. Results:

| | Entry `172.20.200.26` | Exit `172.20.200.25` |
|---|---|---|
| Serial | `BOCK222661069` | `BJ2C184562186` |
| Firmware / platform | Ver 6.60 Oct 2021 / ZLM60_TFT | Ver 6.60 May 2018 / ZMM200_TFT |
| Users / buffered records | 110 / 19,885 | 110 / 20,379 |
| Full-buffer read | **7,641 ms** | **7,234 ms** |
| Clock skew vs bridge | **−154 s** | **−144 s** |
| `punch` values seen | `0` ×55 (all) | `1` ×85, `0` ×3 |
| `status` values seen | `1` ×14, `4` ×41 | `1` ×13, `4` ×75 |

**The mapping correction is confirmed.** `punch` really is the attendance state:
the entry terminal reports `punch=0` for every record and the exit terminal reports
`punch=1` for 85 of 88. The spike would have labelled all of those a verification
method. `status` varies independently with how the person identified, exactly as the
corrected mapping predicts.

Three things the live run surfaced that the design has to answer:

1. **`status=4` is unrecognised and it is the majority case** — 116 of 143 records
   across both terminals. Its meaning is not settled between firmware families
   (documented variously as face, card, or fingerprint+card), so it is deliberately
   left as `UNKNOWN` rather than guessed at: sending a wrong verification method to
   HRMS is worse than sending none. **Open question 7.**
2. **The exit terminal reported 3 records with `punch=0`** — someone pressed the
   check-in key on the exit device. The per-device `role` override (§2) forces these
   to `CHECK_OUT`, which is the intended behaviour for a dedicated entry/exit pair,
   but it is now a confirmed real occurrence rather than a hypothetical.
3. **Both device clocks are ~2.5 minutes behind the bridge**, past the 120 s warning
   threshold. Every punch is currently being recorded ~150 s early. This is not a
   bridge defect and the bridge will not correct it (§5.3), but it must be fixed on
   the terminals before go-live or every attendance record inherits the offset.

**Poll cost is now a measured number, not an estimate:** ~7.5 s per device per poll
for a ~20k-record buffer. Two devices is ~15 s of work per cycle. That comfortably
fits the 5-minute default and confirms that the 1-minute option must carry a warning
— it would leave the bridge reading almost continuously.

### 1.2 Constraint — the ZK protocol has no server-side time filter

`conn.get_attendance()` (`pyzk/base.py:1541`) reads the device's **entire**
attendance buffer, then the caller filters in Python. There is no "give me records
since T" command. Consequences:

- Every 5-minute poll transfers the whole buffer (commonly 10k–100k records,
  ~0.5–5 MB, several seconds on a busy device). This is the dominant cost of the
  system and must be measured, bounded by timeout, and logged per run.
- `get_attendance()` also calls `get_users()` internally on every invocation,
  adding a second full table read.
- Mitigation: keep the poll interval at 5 min (not 1 min) by default on devices
  with large buffers; expose the measured read duration and record count on the
  dashboard; add a configurable device-read timeout that fails the run cleanly
  rather than hanging the scheduler.
- Device-side buffer clearing (`clear_attendance()`) will **not** be implemented.
  It is destructive and irreversible, and the bridge is not the system of record.

### 1.3 Constraint — device timestamps are naive device-local time

`__decode_time` returns a naive `datetime` in the device's own clock. The spike
compares it against `datetime.now()` on the bridge machine. If the device clock
drifts (they do), the sync window silently loses or duplicates records.

Design response in §5.3.

---

## 2. Answers locked in

| Decision | Choice |
|---|---|
| Project location | New sibling repo `d:\Dev\git-repos\Ve\biometric-bridge`. `biometric_test` stays untouched as the reference spike. |
| HRMS integration | Data-driven **HRMS profile** — endpoint, method, auth scheme, field names, batching, datetime format and response parsing are all configuration. Nothing hardcoded. You supply the real contract; I fill in one profile, zero core changes. |
| Punch type source | Per-device `role`: `CHECK_IN` \| `CHECK_OUT` \| `FROM_DEVICE`. Your two devices get `CHECK_IN` / `CHECK_OUT`. `FROM_DEVICE` derives from the record's `punch` field for future mixed devices. |

---

## 3. Technology stack

**Backend** — Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, SQLite,
APScheduler (AsyncIO), HTTPX, Uvicorn, pyzk, pywin32 (DPAPI), passlib/argon2.

**Frontend** — React 19 + TypeScript, built with Vite, styled with Bootstrap 5.
Built to `backend/app/static` and served by FastAPI on the same origin (single port,
no CORS surface). Kept deliberately small — see §9.1.

**Packaging** — PyInstaller one-folder build wrapped by **WinSW** (single vendored
`.exe` + XML config; declarative auto-restart and log rotation, no third-party
installer). NSSM documented as the alternative.

**Explicitly excluded** — Celery, Redis, RabbitMQ, Kafka, Kubernetes, Docker-as-a-
requirement, microservices, cloud database.

---

## 4. Project structure

```
biometric-bridge/
├── backend/
│   ├── app/
│   │   ├── main.py                       # lifespan: config → db → crypto → scheduler → routes
│   │   ├── api/
│   │   │   ├── deps.py                   # auth dependency, db session, current_user
│   │   │   └── routes/
│   │   │       ├── auth.py  devices.py  hrms.py  sync.py
│   │   │       ├── settings.py  users.py  dashboard.py  health.py
│   │   ├── biometric/
│   │   │   ├── base.py                   # BiometricDevice ABC
│   │   │   ├── essl_client.py            # pyzk impl, runs in worker thread
│   │   │   ├── mapping.py                # status/punch → enums, empirically verified
│   │   │   └── device_manager.py         # connector factory + per-device locks
│   │   ├── services/
│   │   │   ├── device_service.py  sync_service.py  hrms_service.py
│   │   │   ├── configuration_service.py  encryption_service.py
│   │   │   ├── health_service.py  backup_service.py  auth_service.py
│   │   ├── hrms/
│   │   │   ├── profile.py                # HrmsProfile pydantic model (the contract shim)
│   │   │   ├── mapper.py                 # BiometricPunch[] → request body per profile
│   │   │   ├── client.py                 # HTTPX, retry, timeout, error classification
│   │   │   └── profiles/default.json     # documented template, NOT a guess at your API
│   │   ├── scheduler/scheduler.py
│   │   ├── database/{database,models,repositories}.py
│   │   ├── schemas/{device,hrms,sync,auth,dashboard}.py
│   │   ├── core/{config,security,logging,errors,paths}.py
│   │   ├── utils/{datetime_utils,network_utils,retry}.py
│   │   └── static/                       # Vite build output (committed artifact)
│   ├── migrations/                       # Alembic
│   └── tests/{unit,integration,fakes}/
├── frontend/                             # React + Vite workspace
│   ├── src/
│   │   ├── api/client.ts                 # fetch wrapper: credentials, 401 → login
│   │   ├── hooks/                        # useDevices, useSyncHistory, useDashboard
│   │   ├── components/                   # StatusBadge, ConfirmDialog, DataTable, Pager
│   │   ├── pages/                        # Dashboard, Devices, Hrms, Sync, History,
│   │   │                                 #   Settings, Users, Logs, Login
│   │   └── main.tsx  App.tsx  routes.tsx
│   ├── index.html  vite.config.ts  package.json  tsconfig.json
├── deployment/
│   ├── install.ps1  uninstall.ps1  start.ps1  stop.ps1  restart.ps1  status.ps1
│   ├── firewall.ps1
│   ├── winsw/BiometricBridge.xml
│   └── build.ps1                         # ng build + pyinstaller + zip
├── docs/{INSTALLATION,CONFIGURATION,HRMS-PROFILE,TROUBLESHOOTING,SECURITY}.md
├── requirements.txt  requirements-dev.txt  pyproject.toml
├── PLAN.md  README.md
```

Rule: route handlers do validation and delegation only. All logic lives in services.

---

## 5. Core design

### 5.1 Device abstraction

```python
class BiometricDevice(ABC):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def test_connection(self) -> ConnectionTestResult: ...
    def get_device_info(self) -> DeviceInfo: ...          # serial, firmware, platform, user/record counts
    def get_device_time(self) -> datetime: ...            # naive, device clock
    def get_attendance_logs(self, from_time, to_time) -> list[RawPunch]: ...
    def get_users(self) -> list[DeviceUser]: ...
    def get_device_status(self) -> DeviceStatus: ...
```

`EsslBiometricDevice` implements it over pyzk. `ZKTeco`/`Matrix` are not implemented now.

**Critical:** pyzk is blocking socket I/O. Every device call is wrapped in
`anyio.to_thread.run_sync(...)` so it never stalls the FastAPI event loop. Getting
this wrong makes the portal freeze for seconds on every poll.

`clear_attendance`, `set_time`, user enrolment and any other write/destructive
device command are deliberately **not** exposed. The bridge is read-only against
hardware.

### 5.2 Normalized model (in-memory only)

```python
class BiometricPunch(BaseModel):
    employee_code: str          # device user_id
    punch_time: datetime        # tz-aware, UTC internally
    punch_type: PunchType       # CHECK_IN | CHECK_OUT | BREAK_IN | BREAK_OUT | OT_IN | OT_OUT
    verification_type: VerificationType  # FINGERPRINT | PASSWORD | CARD | FACE | UNKNOWN
    device_id: int
    device_serial_number: str | None
    raw_status: int             # kept for diagnostics only
    raw_punch: int
    client_record_id: str       # sha256(serial|employee_code|punch_time_utc|raw_punch)[:32]
```

`client_record_id` is deterministic across retries. It is sent to HRMS **only if**
the supplied HRMS contract has a field for it; otherwise it is used purely for
in-run deduplication. It is never invented into the HRMS payload.

Never persisted. Discarded when the sync run ends.

### 5.3 Clock handling — the part that gets attendance systems wrong

1. At sync start, read `device_time` and bridge `now`; compute
   `clock_offset = device_time - bridge_now`.
2. The sync window is expressed in **device clock terms**: `to_time_device = device_time`.
   `from_time_device = watermark_device_equivalent - overlap`.
3. Records are filtered against the window using device-naive comparison (same basis
   as the spike, but now with a device-sourced upper bound rather than the bridge clock).
4. Each surviving record is localized: `naive_device_time → configured timezone
   (Asia/Kolkata) → UTC`. All storage, comparison and API output is tz-aware UTC.
5. If `abs(clock_offset) > drift_warning_seconds` (default 120) the device shows a
   **CLOCK DRIFT** badge on the dashboard and the run logs a warning. The bridge
   never corrects the device clock — that is an operator action.
5a. **Clock regression guard (mandatory).** Drift detection alone is not enough; a
   device clock that *moves* breaks the window arithmetic in two ways:
   - **Backwards jump** (operator corrects a fast device): `to_time = device_now`
     falls below the watermark, the window inverts, and every subsequent run returns
     nothing — silent data loss behind a green dashboard. Guard: if
     `device_now < watermark`, abort the run with status
     `DEVICE_CLOCK_REGRESSION`, leave the watermark untouched, and surface it on
     the dashboard. Never compute an inverted window.
   - **Forwards jump:** the watermark leaps ahead and the skipped interval is never
     pushed. Guard: if `clock_offset` changes by more than
     `max_offset_delta_seconds` (default 300) between consecutive runs, fail with
     `DEVICE_CLOCK_JUMP` rather than advancing. Recovery is an operator-run manual
     sync over the affected window.
   Both statuses are non-retryable — retrying a bad clock does not fix a bad clock.
6. Ambiguous/non-existent local times (DST) are resolved with `fold=0` and logged.
   Asia/Kolkata has no DST, but the code must not assume the configured zone doesn't.

Rule: naive datetimes exist only inside `essl_client.py`. They are illegal at every
service boundary; the linter config and a unit test enforce this.

### 5.4 Incremental sync algorithm

```
for each enabled device:
    acquire per-device lock (else -> 409 / skip)
    open sync_run row (status=RUNNING, started_at=now)
    try:
        watermark  = sync_state.last_successful_push_at          # None on first run
        device_now = device.get_device_time()
        from_time  = (watermark - overlap) if watermark else (device_now - initial_lookback)
        to_time    = device_now
        raw        = device.get_attendance_logs(from_time, to_time)
        punches    = normalize(raw, device.role, tz)             # invalid -> INVALID_RECORD, counted, skipped
        punches    = dedupe_in_run(punches, key=client_record_id)
        if not punches:
            close run SUCCESS / NO_RECORDS ; DO NOT advance watermark ; return
        for batch in chunks(punches, profile.batch_size):
            hrms.push(batch)            # with retry; raises classified error
        watermark = max(p.punch_time for p in punches)           # ONLY after all batches succeed
        persist watermark
        close run SUCCESS
    except ClassifiedError as e:
        close run FAILED with status code ; watermark UNCHANGED
    finally:
        release lock
```

Key rules, restated because they are the ones that cause data loss when violated:

- The watermark is the **max punch time actually accepted by HRMS**, not `to_time`.
  If the device is idle, the watermark does not move.
- The watermark advances **only after every batch in the run succeeded**. A partial
  batch failure leaves it untouched and the whole window is retried next cycle.
  With a per-batch advance you would gain throughput and lose the simple correctness
  argument — not worth it at this volume.
- Overlap (default 60s) means the boundary window is re-sent on every cycle.
  **This depends on HRMS deduplicating.** See §5.5.
- `initial_lookback` (default 24h, configurable) bounds the very first run so a
  device with a 2-year buffer doesn't push 100k records into HRMS on install.
  This is called out explicitly in the install docs as a decision the operator makes.

**Partial acceptance rule (spec §43.19) — REVISED 2026-08-08.** The earlier version of
this rule advanced the watermark only when accepted records formed a contiguous prefix
in time, and otherwise held it, on the grounds that advancing would strand rejected
records behind it. That was written before the HRMS response shape was known, and it is
wrong for one of the two rejection kinds.

The distinction that matters is **"HRMS could not answer" versus "HRMS answered no"**:

| Per-record outcome | Watermark | Counted as |
|---|---|---|
| accepted / inserted | advances | `records_success` |
| duplicate (already held) | advances | `records_success`, reported separately |
| **terminal** reject — unknown employee code, malformed record | **advances** | `records_failed` |
| **retryable** — 429, 5xx, timeout, connection reset | **blocks** | run `FAILED` |

**Rule: any retryable failure in a run leaves the watermark untouched and the whole
window is retried. If every record received a final answer — including "no" — the
watermark advances to the max `punch_time` in the run.**

Stranding a terminally-rejected record is the correct outcome, because retrying it
achieves nothing. Holding the watermark for it is actively harmful: an unenrolled
employee would wedge the device permanently, each cycle re-reading a wider window and
re-sending every punch in it to fail on the same record. **One unenrolled employee
would stop attendance for everybody.**

Terminal failures are surfaced instead of retried — counted in
`sync_runs.records_failed`, shown on the dashboard with the offending employee code,
and logged. An unenrolled employee becomes a visible task for HR rather than a silent
stall. The original safety property is untouched: HRMS being down, slow or erroring
still blocks the watermark, which was the point.

Full reasoning and the per-record response contract this depends on:
[docs/ATTENDANCE-INGESTION.md](docs/ATTENDANCE-INGESTION.md) §5.

**All-invalid window.** If every record in the window fails normalization, the run
closes `INVALID_RECORD` (not `NO_RECORDS`) so it is visibly different from a genuinely
idle device, and the watermark does not move.

### 5.5 HRMS idempotency — RESOLVED 2026-08-08, and the answer is "no"

The original assumption was:

> The overlap window and the retry policy both re-send punches that HRMS may have
> already accepted. The design assumes the existing HRMS attendance endpoint is
> **idempotent** for a repeated punch (same employee, same timestamp, same type).

**That assumption is false.** HRMS was reviewed on 2026-08-08 — see
[docs/HRMS-REVIEW.md](docs/HRMS-REVIEW.md). Both write paths (`AddAttendance`,
`PostLatestWebCheckIn`) append a log row unconditionally, and `EmpAttendanceLogs` has
a primary key and three foreign keys but **no unique index**. Nothing deduplicates.

The review also established the larger finding: **HRMS has no biometric ingestion
endpoint at all.** `CheckInTypesEnum.Biometric = 1` is seeded in the lookup table and
never written by any code path. There is no contract to adapt to — one has to be
defined.

Consequences for this design:

- The 60 s overlap in §5.4 would create duplicate attendance rows on every cycle
  against the current API. It cannot be used as-is.
- Retry-after-timeout (§43.20) has the same problem from the same root.
- Dropping to `overlap = 0` + strict `punch_time > watermark` is the only
  bridge-side mitigation, and it is a poor one here: both terminals measured ~150 s
  behind the bridge clock (§1.1), which is exactly the condition under which a
  zero-overlap window silently loses punches.

**The real fix is a dedicated ingestion endpoint that deduplicates** — Option A in the
review's §5, and the recommendation. That requires an HRMS change, which the original
brief forbade. The brief forbade it on the assumption a contract already existed; it
does not. **This is now a decision for you, not an assumption the plan can make.**

Until it is made, Phase 3 is blocked. Phases 1, 2, 4 and 5 are unaffected — they build
and test against the fake HRMS.

### 5.6 Concurrency and locking

Two layers:
- **In-process:** `asyncio.Lock` per device id, held for the whole run.
- **Persisted:** `sync_states.locked_at` + `lock_owner` (process id + start time),
  with a configurable stale-lock TTL (default 30 min).

On startup, any `sync_run` still in `RUNNING` from a previous process is closed as
`INTERRUPTED` and its lock cleared — this is what makes "Windows restart mid-sync"
(§43.10/11) recover cleanly instead of wedging the device forever.

Manual sync competes for the same lock and returns **409 Conflict** with a clear
message when the scheduler holds it.

APScheduler jobs use `max_instances=1`, `coalesce=True`, `misfire_grace_time=60`.

### 5.7 Sync triggers — explicit taxonomy (spec RULE 5)

Three ways a sync starts, with **different watermark semantics**. Naming them is what
keeps RULE 5 from being violated by accident:

| Trigger | Started by | Window | Watermark |
|---|---|---|---|
| `SCHEDULED` | APScheduler | computed (§5.4) | **advances** on success |
| `CATCH_UP` | operator, "Sync Now" on a device row | computed — identical to `SCHEDULED` | **advances** on success |
| `MANUAL_RANGE` | operator, explicit From/To on the Sync page | operator-supplied | **never touched** |
| `STARTUP` | recovery of an interrupted run | computed | **advances** on success |

`CATCH_UP` is deliberately just "run the scheduled sync now instead of waiting" — same
code path, same window, same watermark behaviour. `MANUAL_RANGE` is the
historical/recovery operation and is the only one that can reach backwards in time; it
is read-and-push only and leaves `last_successful_push_at` exactly as it found it.

Every `sync_run` row records its trigger, so history shows which is which and the
watermark behaviour of any past run is auditable.

`MANUAL_RANGE` validation: `from < to`, window bounded by a configurable max span
(default 31 days), device must be enabled, `to` may not be in the future relative to
the device clock.

### 5.8 Retry and error classification

Retryable: connection error, read timeout, HTTP 5xx, HTTP 429, device temporarily
unavailable. Backoff 5s → 15s → 30s (configurable, jittered), then fail the run.

Not retryable: 401/403 (`HRMS_AUTHENTICATION_FAILED`), 400/422
(`HRMS_BAD_REQUEST`), device config invalid, malformed record.

Statuses: `SUCCESS`, `NO_RECORDS`, `PARTIAL`, `FAILED`, `DEVICE_UNAVAILABLE`,
`HRMS_UNAVAILABLE`, `HRMS_AUTHENTICATION_FAILED`, `HRMS_BAD_REQUEST`,
`DEVICE_CONFIGURATION_ERROR`, `DEVICE_CLOCK_REGRESSION`, `DEVICE_CLOCK_JUMP`,
`INVALID_RECORD`, `TIMEOUT`, `INTERRUPTED`, `UNKNOWN_ERROR`.

**Already-pushed suppression.** A bounded in-memory (never persisted) LRU of
`client_record_id`s pushed during this process lifetime suppresses re-sending the
overlap window's records on every cycle. Without it an idle device re-pushes its last
punch roughly 150 times overnight. This also reduces — but does not remove — the
design's dependence on HRMS idempotency (§5.5), since the LRU is empty after a
restart.

Portal shows a clean message; stack traces go to the log file only.

---

## 6. HRMS profile (the contract shim)

This is how "do not invent the contract" is honoured. The client is generic; one
JSON profile describes your real API:

```jsonc
{
  "attendance_path":      "/api/...",              // YOU SUPPLY
  "method":               "POST",
  "auth": {
    "scheme":             "header",                 // header | bearer | query
    "header_name":        "X-API-Key",
    "value_template":     "{api_key}"
  },
  "tenant": { "header_name": null, "value": null },
  "extra_headers":        {},
  "batch": {
    "mode":               "array",                  // array | envelope | single
    "envelope_path":      "records",                // used when mode=envelope
    "size":               200
  },
  "field_map": {
    "employee_code":      "employeeCode",
    "punch_time":         "punchTime",
    "punch_type":         "punchType",
    "verification_type":  "verificationType",
    "device_serial":      null,                     // null = omit field entirely
    "client_record_id":   null
  },
  "value_map": {
    "punch_type":         { "CHECK_IN": "CHECK_IN", "CHECK_OUT": "CHECK_OUT" },
    "verification_type":  { "FINGERPRINT": "FINGERPRINT", "PASSWORD": "PASSWORD" }
  },
  "datetime_format":      "iso8601_offset",         // iso8601_offset | iso8601_utc_z | strftime pattern
  "response": {
    "success_status_codes": [200, 201, 202],
    "success_path":         null,                   // optional JSONPath-lite, e.g. "success"
    "processed_count_path": null,
    "failed_count_path":    null,
    "error_message_path":   null
  },
  "test_connection": { "path": null, "method": "GET" }
}
```

`field_map` entries set to `null` are omitted from the payload — so employee names
and any field your API doesn't want are never sent. The shipped
`profiles/default.json` is a **documented template with placeholder paths**, not a
guess at your endpoint; the bridge refuses to enable HRMS sync until a profile with
a real `attendance_path` is configured.

`docs/HRMS-PROFILE.md` will explain each key with worked examples, so wiring the
real contract is a config edit plus one integration test.

---

## 7. Database schema

SQLite at `C:\ProgramData\BiometricBridge\data\biometric_bridge.db`, WAL mode,
Alembic migrations.

**devices** — id, name, ip_address, port, serial_number, location, device_type,
`role` (CHECK_IN/CHECK_OUT/FROM_DEVICE), `comm_key_protected` (the ZK device
communication password — goes through the same `SecretProtector` seam as the HRMS
key; this is the device's comm key, **not** any employee credential, and §0's
"no device passwords stored" refers to the latter), timeout_seconds, force_udp,
timezone (per-device, defaults to the app timezone — needed for multi-site),
enabled, created_at, updated_at.

**hrms_connections** — id, base_url, api_key_protected (BLOB, currently pass-through
per §8), api_key_last4, tenant_identifier, profile_json, connect_timeout,
request_timeout, enabled, created_at, updated_at.

**sync_states** — id, device_id (unique), last_successful_push_at,
last_sync_started_at, last_sync_completed_at, last_status, last_error,
locked_at, lock_owner, clock_offset_seconds, updated_at.

**sync_runs** — id, device_id, trigger (SCHEDULED/CATCH_UP/MANUAL_RANGE/STARTUP),
started_at, completed_at, from_time, to_time, `buffer_records` (whole device buffer —
the §1.2 perf metric), `window_records` (in-window after filtering), records_sent,
records_success, records_failed, records_invalid, records_suppressed,
device_read_ms, hrms_ms, status, error_message.

**app_settings** — key/value: timezone, sync_interval_minutes, sync_overlap_seconds,
auto_sync_enabled, initial_lookback_hours, retry policy, drift threshold,
backup retention.

**users** — id, username, password_hash (argon2), role, is_active, last_login_at,
failed_attempts, locked_until.

**No table stores punches.** A test asserts no model contains a punch-shaped column,
so this can't regress silently.

Retention: `sync_runs` pruned to a configurable window (default 180 days).

---

## 8. Security

- **API key at rest: NOT encrypted — decided 2026-08-08, deferred not cancelled.**
  The HRMS API key is stored as-is in the SQLite file. Consequences, stated plainly:
  anyone with read access to `C:\ProgramData\BiometricBridge\data\` or to any
  backup copy can recover the HRMS credential. Directory ACLs (SYSTEM +
  Administrators) are the only control. This reverses spec RULE 6 / §10 / §41 at the
  customer's direction.
  - **Seam kept so this is a later switch, not a rewrite:** all reads/writes of the
    key go through a `SecretProtector` interface with two methods, `protect()` and
    `reveal()`. The shipped implementation is `NullSecretProtector` (pass-through).
    Adding `DpapiSecretProtector` later is a config flag plus a one-off re-wrap of
    existing rows — the column type (`BLOB`) and every call site stay unchanged.
- Regardless of encryption, the key is still **never** returned by any API, never
  rendered in the UI, never logged and never placed in an exception message. A
  logging filter redacts it from any log record as defence in depth.
- GET config returns `api_key_masked: "••••••••1234"` and `api_key_set: true` only.
- **Portal auth:** local users, argon2id hashes, opaque session id in an httpOnly +
  SameSite=Strict cookie. Login rate-limited (5 attempts / 15 min per username+IP,
  in-process). First-run bootstrap forces creation of the admin account and rejects
  weak passwords — no shipped default credentials.
- **Portal transport: plain HTTP over the LAN. Decided — no TLS, no certificate.**
  Consequences, stated plainly rather than left implicit:
  - The cookie ships **without** the `Secure` flag and there is no HSTS.
  - The session cookie and the login password are **readable by anyone who can sniff
    LAN traffic** between an operator's browser and the bridge. Portal access is
    therefore only as strong as the physical/switched network it runs on.
  - The HRMS API key is never sent to the browser (§8, masked-only), so a sniffed
    session grants portal access but does not directly disclose the HRMS credential.
  - Mitigation available later without redesign: drop a self-signed or internal-CA
    cert into `config\` and flip one setting; the code sets `Secure`/HSTS from that
    flag rather than hardcoding.
- Same-origin frontend, so CORS stays disabled by default (configurable allow-list if
  the portal is ever hosted separately).
- Security headers: X-Content-Type-Options, X-Frame-Options DENY, Referrer-Policy,
  strict CSP for the SPA. HSTS only if TLS is later enabled.
- Bind defaults to `0.0.0.0:8080` for LAN access; the install script adds an inbound
  rule **scoped to the local subnet**, not Any.
- SQLAlchemy parameterized queries throughout; Pydantic validation on every input.
- HTTPS enforced for HRMS base URL (plain `http://` rejected unless explicitly
  overridden for a lab environment).

---

## 9. Web portal

### 9.1 Stack — deliberately small

| Layer | Choice | Why |
|---|---|---|
| Framework | React 19 + TypeScript | |
| Build | Vite | Fast, minimal config. Not CRA (dead), not Next (SSR is pointless here) |
| Routing | React Router | ~10 KB |
| UI | Bootstrap 5 CSS + react-bootstrap | Bootstrap's own JS is imperative and fights React; react-bootstrap reimplements the widgets natively against the same CSS, tree-shaken to what's imported |
| Server state | TanStack Query | ~13 KB, and it is what makes the polling dashboard and post-mutation refresh cheap to write |
| Client state | `useState` / `useReducer` | |
| Forms | Controlled components + a small shared `useForm` helper | |

**Deliberately excluded:** Redux/Zustand (no client state worth centralising), Next.js,
Tailwind (Bootstrap is already the design system), MUI, any charting library — the
dashboard is counters, badges and timestamps.

Expected payload ≈ 100 KB gzipped total.

**Everything is bundled — no CDN references.** Per §32 the bridge only needs outbound
HTTPS to HRMS; many of these machines have no general internet access. Bootstrap CSS,
fonts and icons all ship inside the build, which also keeps the CSP in §8 strict.

Node/npm are **build-time only**. The shipped artifact is static files inside the
PyInstaller bundle; the target machine never needs Node. `backend/app/static` is
committed so the backend can be built and run without the frontend toolchain present.

Auth is a plain httpOnly session cookie, so the frontend holds no token and does no
header management — the fetch wrapper only needs `credentials: 'include'` and a 401 →
login redirect.

### 9.2 Pages

Dashboard, Devices, HRMS Configuration, Synchronization (manual run),
Sync History, Settings, Users, Logs/About.

**Dashboard** — device summary (total/connected/disconnected/disabled), HRMS status,
last successful sync, last failed sync, last successful push per device, scheduler
state, today's run/read/pushed/failed counts (all from `sync_runs`, never from stored
punches), last error, clock-drift warnings.

**Devices** — table with name, IP, port, serial, location, role, status, enabled,
last sync, actions (add/edit/delete/enable/disable/test connection/sync now).
Add/Edit form includes an inline **Test Connection** that reports firmware, serial,
user count, record count and device clock offset before saving.

**Sync History** — filterable (date range, device, status, trigger), paginated,
showing counts, duration and status badge. No punch detail, by design.

Bootstrap components only; confirmation dialogs on delete, disable and manual sync.

---

## 10. REST API

```
POST   /api/auth/login          POST /api/auth/logout        GET /api/auth/me
GET    /api/devices             POST /api/devices
GET    /api/devices/{id}        PUT  /api/devices/{id}       DELETE /api/devices/{id}
POST   /api/devices/test-connection        # body-based, for an UNSAVED device form
POST   /api/devices/{id}/test-connection   # saved device
GET    /api/devices/{id}/status
GET    /api/hrms/config         PUT  /api/hrms/config        POST /api/hrms/test-connection
GET    /api/sync/status         GET  /api/sync/history
POST   /api/sync/catch-up                  # all enabled devices — advances watermark
POST   /api/sync/catch-up/{device_id}      # one device  — advances watermark
POST   /api/sync/manual-range/{device_id}  # explicit From/To — NEVER touches watermark
GET    /api/settings            PUT  /api/settings
GET    /api/users               POST /api/users              PUT /api/users/{id}
GET    /api/dashboard
GET    /api/logs                           # tail of the application log, paginated
GET    /health                  GET  /health/detailed
```

Sync endpoint paths are named after their **watermark semantics**, not after who
clicked them, so the dangerous one (`manual-range`) cannot be invoked by accident from
a "Sync Now" button. See §5.7.

`/health` is unauthenticated and returns only `{"status":"healthy"}` — it leaks
nothing and is what the installer and service watchdog poll. `/health/detailed`
requires auth.

---

## 11. Windows service and deployment

Data root `C:\ProgramData\BiometricBridge\` with `data\ logs\ config\ backups\`.
Never a temp directory. Logs: rotating file handler, JSON-structured, 10 MB × 10.

`deployment/build.ps1` → `npm ci && npm run build` (Vite, output to
`backend/app/static`), then PyInstaller one-folder, then zip with the WinSW exe/XML
and scripts. Node 22 is required on the **build** machine only.

`install.ps1` — create directories with correct ACLs, copy payload, register the
service (auto-start, restart-on-failure), add the LAN-scoped firewall rule, start,
then poll `/health` and report the portal URL. `uninstall.ps1` stops and removes the
service and firewall rule and **leaves `ProgramData` intact** (prompt before any
data deletion). Plus `start/stop/restart/status.ps1` and `firewall.ps1`.

**Startup order** (must not block): load config → init DB/migrations → init crypto →
close orphaned `RUNNING` runs → register scheduler jobs → serve HTTP → begin
async connectivity probes. A dead device or an unreachable HRMS must never prevent
the service from starting; that's what the health monitor is for.

**Backups:** manual + optional scheduled SQLite backup via the online backup API
(safe under WAL), configurable retention, encrypted key material backed up as
ciphertext only — never decrypted into a backup file.

Firewall: inbound TCP 8080 from LAN subnet only; outbound TCP 443 to HRMS; outbound
TCP/UDP 4370 to devices. Documented in `docs/INSTALLATION.md`.

---

## 12. Testing

Target ≥80% backend coverage, enforced in CI config.

- **Fake device** implementing `BiometricDevice` returning scripted buffers — lets
  every sync scenario run without hardware.
- **Fake HRMS** via `httpx.MockTransport` — success, 401, 400, 500, 429, timeout,
  partial response, empty response.
- **Sync engine** (the priority suite): first sync, incremental, overlap boundary,
  watermark advances only on full success, **watermark unchanged on HRMS failure**,
  empty result doesn't advance, retry then success, retry exhaustion, concurrent-sync
  409, manual sync leaves watermark untouched, multi-device independence, interrupted
  run recovery, clock-skew windows, malformed record handling, batch chunking.
- **Security:** login valid/invalid, lockout, unauthorized access, API key never in
  any response body or log line (assert over captured log output), encrypt/decrypt
  round-trip.
- **Invariant test:** no ORM model persists punch data.
- **Live smoke script** (`tests/manual/live_device_check.py`) against 172.20.200.25/.26
  to confirm the `status`/`punch` mapping from §1.1 on real hardware.

---

## 13. Phases

| # | Phase | Deliverable | Done when |
|---|---|---|---|
| 1 | Foundation | Repo, config, paths, logging + secret redaction, SQLite + Alembic, `/health` | App starts, health responds, migrations apply |
| 2 | **Auth** | Users table, argon2id hashing, session cookie, login rate limit, route guards, security headers | Security suite green; every `/api/*` route refuses anonymous access |
| 3 | Device layer | `BiometricDevice`, `EsslBiometricDevice`, mapping verified on live hardware, device CRUD + test-connection | Both live devices connect; §1.1 mapping confirmed |
| 4 | HRMS client | Profile model, mapper, HTTPX client, error classification, test-connection | Fake-HRMS integration tests green |
| 5 | Sync engine | Watermark, overlap, batching, retry, locking, sync_runs | Full sync test suite green |
| 6 | Scheduler | APScheduler, interval config, no-overlap, startup recovery | Unattended multi-cycle run clean |
| 7 | Portal | React + Vite app, all pages, built into `app/static`, served by FastAPI | Full operator workflow through UI |
| 8 | Testing | Coverage to ≥80%, edge cases from §43 | Coverage gate passes |
| 9 | Packaging | build.ps1, PyInstaller, WinSW, install scripts | Clean-machine install → portal reachable after reboot |
| 10 | Hardening | Backups, retention, docs, troubleshooting | All 24 acceptance criteria demonstrated |

Tests run and pass at the end of every phase. No phase proceeds on known-broken
functionality.

**Phase 3 is gated on you supplying the HRMS contract.** Phases 1, 2, 4, 5 can be
built and fully tested against the fake HRMS before that arrives, so the gate costs
no schedule time — but Phase 3 will not be guessed at.

---

## 14. Open questions

1. ~~**HRMS attendance API contract**~~ — **answered 2026-08-08: there is no contract.**
   HRMS has no biometric ingestion endpoint; `CheckInTypesEnum.Biometric = 1` is a
   seeded lookup value that nothing writes. See [docs/HRMS-REVIEW.md](docs/HRMS-REVIEW.md).
   **Superseded by Q1a below.**
   - **Q1a — Option A or Option B?** (review §5) Option A = a new API-key ingestion
     endpoint in HRMS that batches and deduplicates; the bridge then works exactly as
     planned. Option B = a service-account JWT against the existing
     `POST api/v1/attendence`, no HRMS change, but one request per punch, no dedup,
     no `EmpCode → EmpId` lookup, and forced `overlap = 0`. **Recommendation: A.**
     Blocks Phase 3; blocks nothing else.
2. ~~**HRMS idempotency**~~ — **answered 2026-08-08: not idempotent.** No unique index
   on `EmpAttendanceLogs`, both write paths append unconditionally. Recorded in §5.5.
3. ~~**Employee code mapping**~~ — **answered 2026-08-08 by the customer: the device
   `user_id` field IS `Employees.EmpCode`** (`IQ-1004` → Manoj Settebathula). Resolution
   is a direct tenant-scoped, case-insensitive lookup; the mapping table proposed in
   HRMS-DB-CHANGES.md §3 is cancelled. Two data questions remain, both answered by
   queries in that document: is `EmpCode` populated for every enrolled employee, and is
   it unique within a tenant? A device user with no match is a **terminal** per-record
   failure, not a batch failure — see
   [docs/ATTENDANCE-INGESTION.md](docs/ATTENDANCE-INGESTION.md) §5.
4. **Initial lookback** — how far back should the first sync on a new device reach?
   Plan defaults to 24h.
5. ~~**TLS for the portal**~~ — **resolved 2026-08-08: plain HTTP on the LAN, no
   certificate.** Consequences recorded in §8. API key encryption at rest is
   unaffected and stays as planned.
6. **Devices in scope at go-live** — plan assumes the two known devices; multi-site
   (Hyderabad/Bangalore) is supported by design but changes install topology.
7. ~~**What does `status=4` mean on these terminals?**~~ — **moot for ingestion as of
   2026-08-08.** `EmpAttendanceLogs` has no verification-mode column and the design does
   not add one: 81% of observed punches report `status=4`, whose meaning is unsettled
   between the two firmware families in use, and writing a guessed label into an
   attendance ledger is worse than writing nothing. The value stays in bridge
   diagnostics, where it is useful for debugging and harmless if wrong. Revisit only if
   HR asks for it. See [docs/ATTENDANCE-INGESTION.md](docs/ATTENDANCE-INGESTION.md) §8.
8. **Device clocks are ~150 s slow** (§1.1). Operator action on the terminals, not a
   code change, but it should happen before the first production sync.
9. **HRMS defect D3 — `ConstructTotalHours` throws on an out-first day.** Not a bridge
   defect and not a bridge fix, but biometric ingestion turns it from rare into
   routine (the exit terminal already logs `punch=0` records). It should be fixed on
   the HRMS side before go-live. Reported in [docs/HRMS-REVIEW.md](docs/HRMS-REVIEW.md) §4.
10. **HRMS defect D4 — the vendor API-key path disables tenant scoping.** Directly
   load-bearing if Option A is chosen, because an API-key request currently resolves
   to an empty `DataKey`, which both matches every tenant on read and would stamp
   unreadable rows on write. Tenant-from-API-key has to be solved before any
   API-key ingestion route can work.
11. **Night shift** — does anyone on these terminals work past midnight?
   `EmployeeAttendance.Date` is a calendar date, and `Shifts` carries an
   `IsOvernightShift` flag that **nothing can use**: a solution-wide search for
   `ShiftId` / `EmployeeShift` / `ShiftAssignment` returns nothing, so shifts are
   defined and never assigned. An overnight shift therefore splits across two
   attendance rows and `ConstructTotalHours` will not pair it. Pre-existing, not
   caused by ingestion, but ingestion makes it visible. See
   [docs/ATTENDANCE-INGESTION.md](docs/ATTENDANCE-INGESTION.md) §6.
12. **HRMS defect D6 — `ConstructTotalHours` orders by insertion, not by punch time.**
   `OrderBy(x => x.Id)` equals time order only while a human presses a button and the
   row is written immediately. Manual historical sync (§5.7) and offline-device
   catch-up both write older punches after newer ones, which scrambles the In/Out
   pairing and silently produces wrong totals. Should be fixed alongside D3 — same
   20-line method, both become routine on day one of ingestion.

None of these block starting Phase 1.
