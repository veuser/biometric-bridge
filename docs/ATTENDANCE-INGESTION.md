# Attendance Ingestion — Design

**Date:** 2026-08-08 · scopes **Option A** ([HRMS-REVIEW.md](HRMS-REVIEW.md) §5)
**Covers:** how one scan on a terminal becomes one row in `AttSch.EmpAttendanceLogs`.

Companion to [HRMS-DB-CHANGES.md](HRMS-DB-CHANGES.md) (schema) and
[PLAN.md](../PLAN.md) (the bridge). No code written yet — HRMS implementation was
paused pending this design.

---

## 1. Identity — resolved

**Confirmed by the customer: the device `user_id` field is `Employees.EmpCode`.**

From the live capture:

```
Timestamp              User ID     Name                  Event
2026-08-08 12:07:09    IQ-1004     Manoj Settebathula    Check Out
```

`IQ-1004` is the `EmpCode`. This closes PLAN.md open question 3 and cancels the
conditional `EmployeeBiometricIds` mapping table in HRMS-DB-CHANGES.md §3 — resolution
is a direct lookup.

```
Employees.EmpCode  ==  device user_id        (tenant-scoped, case-insensitive)
```

Two conditions still have to hold, and both are data questions the queries in
HRMS-DB-CHANGES.md §2 answer:

- every enrolled employee has a non-empty `EmpCode`;
- no two employees in a tenant share one (including case variants).

An employee enrolled on a terminal but missing an `EmpCode` produces punches that can
never be attributed. That is a **terminal** failure — see §5.

---

## 2. The pipeline

| Field | Source | Becomes |
|---|---|---|
| `user_id` = `IQ-1004` | device | → `Employees.EmpCode` → `EmployeeAttendance.EmpId` |
| naive device timestamp | device | → bridge: device-local → tenant tz → **UTC instant** |
| device role (entry / exit) | bridge config | → `EmpAttendanceLogs.PunchTypeId` (1 In / 2 Out) |
| — | fixed | → `EmpAttendanceLogs.CheckInTypeId` = 1 (Biometric) |
| device serial | bridge config | → `EmpAttendanceLogs.DeviceSerialNumber` |
| computed hash | bridge | → `EmpAttendanceLogs.SourceRef` |
| UTC instant | **HRMS derives** | → `EmployeeAttendance.Date` (tenant-local calendar date) |
| UTC instant | **HRMS derives** | → `Hour` / `Minute` / `Second` (tenant-local wall clock) |
| UTC instant | as sent | → `PunchTimeUtc` |
| verification type | device | → **dropped**, see §8 |

---

## 3. The load-bearing rule

> **The bridge sends one UTC instant per punch. HRMS derives the date and the wall
> clock from it. The bridge never sends `Date`, `Hour`, `Minute` or `Second`.**

This is the single most important decision in the design, and it exists because the
codebase already contains the counter-example.

Web check-in lets the *client* decide. The browser sends `date: new Date()` and
`hours: now.getHours()`, and `PostLatestWebCheckIn` stores all of it verbatim. Meanwhile
`GetLatestWebCheckIn` computes the day from the **tenant's** timezone, under an explicit
comment saying a 23:30 local punch must land on the local day. Write and read therefore
compute the day by two different rules and disagree near midnight — defect D1.

Biometric ingestion must not inherit that. Deriving both values server-side from a
single instant means:

- one source of truth per punch, so the day and the clock time cannot contradict
  each other;
- the tenant calendar is applied by the same `ITenantLocaleService` the read path
  already uses, so `getdetailAttendence` and the ingested row agree by construction;
- changing a tenant's timezone later reinterprets new punches correctly without the
  bridge being redeployed.

The bridge has *better* clock information than a browser does — it measures each
terminal's offset — but it still is not the authority on the tenant's calendar. It
reports an instant; HRMS decides what day that was.

---

## 4. The contract

```http
POST /api/v1/attendence/biometric
api_key: <secret>
Content-Type: application/json
```

```jsonc
{
  "deviceSerialNumber": "BOCK222661069",
  "punches": [
    {
      "employeeCode": "IQ-1004",
      "punchTimeUtc":  "2026-08-08T06:37:09Z",
      "punchType":     "OUT",              // IN | OUT
      "sourceRef":     "a3f1c9e28b7d…"     // 64 chars max, stable across retries
    }
  ]
}
```

Response — **always per-record**, so the bridge can report honest counts:

```jsonc
{
  "deviceSerialNumber": "BOCK222661069",
  "received":  45,
  "inserted":  43,
  "duplicate":  1,
  "failed":     1,
  "results": [
    { "sourceRef": "a3f1…", "status": "Inserted" },
    { "sourceRef": "b7c2…", "status": "Duplicate" },
    { "sourceRef": "c9d4…", "status": "UnknownEmployee", "employeeCode": "IQ-9999" }
  ]
}
```

| HTTP | Meaning | Bridge behaviour |
|---|---|---|
| `200` | Batch processed. Per-record outcomes in `results`. | Read `results`; see §5 |
| `400` | Envelope malformed (bad JSON, missing `punches`) | Terminal — bug, do not retry |
| `401` | Key invalid, revoked or expired | Terminal — `HRMS_AUTHENTICATION_FAILED` |
| `403` | Key lacks `attendance.write` scope | Terminal — `HRMS_AUTHENTICATION_FAILED` |
| `429`, `5xx` | Transient | **Retryable** — backoff and retry |

Per-record `status` values: `Inserted`, `Duplicate`, `UnknownEmployee`, `Invalid`
(unparseable timestamp, unknown punch type, blank employee code).

A batch containing one bad record is **not** a `400`. Rejecting 200 good punches
because one employee is unenrolled would be a self-inflicted outage.

---

## 5. Terminal vs retryable — a correction to PLAN.md §5.4

The plan currently says the watermark advances only after **every** record in the run
succeeds. Combined with per-record failures that is a trap:

> An unknown `EmpCode` never succeeds. Under the current rule the watermark never
> advances, so every subsequent cycle re-reads an ever-widening window, re-sends every
> punch in it, and fails again on the same record — **permanently wedged, and getting
> slower**. One unenrolled employee stops attendance for everybody.

The rule needs to distinguish *"HRMS could not answer"* from *"HRMS answered no"*:

| Outcome | Watermark | Counted as |
|---|---|---|
| `Inserted` | advances | `records_success` |
| `Duplicate` | advances | `records_success` (reported separately) |
| `UnknownEmployee`, `Invalid` | **advances** | `records_failed` |
| `429`, `5xx`, timeout, connection error | **blocks** | run marked `FAILED` |

**Revised rule:** *any retryable failure in a run leaves the watermark untouched and
the whole window is retried. If every record got a final answer — including "no" — the
watermark advances to the maximum punch time in the run.*

Retrying a record HRMS has already definitively rejected achieves nothing except
blocking every punch behind it. Terminal failures are surfaced instead: counted in
`sync_runs.records_failed`, listed on the dashboard with the offending employee code,
and logged. An unenrolled employee becomes a visible task for HR, not a silent stall.

This also preserves the original safety property. HRMS being down, slow, or erroring
still blocks the watermark — which was the point of the rule.

---

## 6. Day assignment, and night shifts

`EmployeeAttendance.Date` is the **tenant-local calendar date** of the punch instant,
via `ITenantLocaleService` — the same call `GetLatestWebCheckIn` already makes.

That is a straight calendar date, not a shift-aware work date. So a night-shift punch
at 01:00 files under the new calendar day, splitting one shift across two
`EmployeeAttendance` rows, and `ConstructTotalHours` — which pairs In/Out within a
single day — will not pair them.

**This cannot be solved today.** `Shifts` exists with a `StartTime`, `EndTime` and an
`IsOvernightShift` flag, but a solution-wide search for `ShiftId`, `EmployeeShift` and
`ShiftAssignment` returns **nothing**. Shifts are defined and never assigned to anyone,
so there is no way to know which shift a punch belongs to.

Calendar date is therefore the only attribution consistent with the existing model, and
it matches what web check-in already does. Recorded as an open question rather than
worked around: inventing shift attribution here would be guessing at a feature that
has not been built.

**If any employee on these terminals works past midnight, raise it before go-live** —
their hours will be wrong, and that is a pre-existing property of the attendance model
rather than something ingestion introduces.

---

## 7. Two more required changes this surfaces

### 7.1 Unique index on `EmployeeAttendance (EmpId, Date)` — **required**

`EmployeeAttendance` has plain indexes on `DataKey`, `EmpId` and `StatusTypeId` and
**no unique constraint on `(EmpId, Date)`**. Both write paths do find-then-create
(`GetEmpAttandance` → `FirstOrDefaultAsync` → `Add`), which races.

This defeats the child dedup index. That index keys on
`(EmpAttendanceId, PunchTypeId, Hour, Minute, Second)` — if two parent rows exist for
the same employee and day, the same punch inserts cleanly under each, because the
`EmpAttendanceId` differs. **The parent must be unique or the child constraint is
decorative.**

```sql
CREATE UNIQUE INDEX "UX_EmployeeAttendance_Emp_Date_Active"
    ON "AttSch"."EmployeeAttendance" ("EmpId", "Date")
    WHERE "StatusTypeId" = 1;
```

Unlike the biometric index, this one covers **existing rows** and can fail on live
data. Preflight:

```sql
SELECT "DataKey", "EmpId", "Date", count(*), array_agg("Id")
FROM   "AttSch"."EmployeeAttendance"
WHERE  "StatusTypeId" = 1
GROUP  BY 1, 2, 3
HAVING count(*) > 1;
```

Any rows returned need merging (re-point child logs at the survivor, deactivate the
rest) before the index can be created. Ingestion also needs an upsert
(`ON CONFLICT DO NOTHING` + re-read) rather than find-then-create, or the race simply
turns into an exception under load.

### 7.2 `ConstructTotalHours` orders by insertion, not by time — **fix before go-live**

```csharp
lst = lst.OrderBy(x => x.Id).ToList();      // AttendenceRepository.cs:214
```

and `GetDetailLogForEmployee` orders by `log.CreatedDate ascending`. Both are
**insertion order**, which happens to equal punch order today because a human presses
the button and the row is written immediately.

Ingestion breaks that assumption on day one. A manual historical sync — an explicitly
supported feature (PLAN.md §5.7) — writes older punches *after* newer ones. A device
that was offline and catches up does the same. Insertion order then has nothing to do
with punch order, the In/Out pairing walks the day out of sequence, and the computed
totals are wrong. Silently: no error, just bad numbers feeding payroll.

With `PunchTimeUtc` present this is a one-line fix (order by it, falling back to
`Hour`/`Minute`/`Second` for rows that predate the column). It should land with the
ingestion endpoint, not after it.

This compounds with defect D3 in the review (`ConstructTotalHours` throws when a day's
first punch is an `Out`). Both live in the same 20-line method, both become routine the
moment biometric data flows, and both should be fixed together.

---

## 8. Verification type — dropped

The terminals report a verification mode (`status`: fingerprint / password / card /
face). `EmpAttendanceLogs` has no column for it.

**Not sending it.** Adding a column to carry a value nobody has asked for is not
justified, and 81% of observed punches report `status=4`, whose meaning is not settled
between the two firmware families in use (PLAN.md §1.1). Writing a guessed label into
an attendance ledger is worse than writing nothing.

`Remarks` and `CustPropData` could carry it if HR ever asks. Until then it stays in the
bridge's diagnostics, where it is useful for debugging and harmless if wrong.

This makes PLAN.md open question 7 moot for ingestion — it only ever mattered if the
contract wanted the field.

---

## 9. Volume

110 users per terminal, two terminals, ~220 punches on a full day. At the 5-minute
default that is a handful of records per poll, with the morning and evening peaks
concentrating perhaps 60–80 into a single cycle.

Batch size 200 (PLAN.md §6) means a normal poll is one request and a full-day manual
recovery is two. The dominant cost stays the ~7.5 s full-buffer device read (PLAN.md
§1.1), not HRMS. No pagination or streaming needed.

---

## 10. Updated required-change list

Supersedes the table in HRMS-DB-CHANGES.md §Summary.

| # | Change | Status |
|---|---|---|
| 1 | `SourceRef`, `DeviceSerialNumber`, `PunchTimeUtc` on `EmpAttendanceLogs` | Required |
| 2 | Partial unique index on the biometric punch natural key | Required |
| 3 | Unique index on `Employees (DataKey, lower(EmpCode))` | Required — preflight |
| 4 | `IntegrationApiKeys` table | Required |
| 5 | **Unique index on `EmployeeAttendance (EmpId, Date)`** | **Required — preflight** (§7.1, new) |
| 6 | Fix inverted `CheckInTypes` seed (ids 2/3) | Recommended |
| 7 | ~~`EmployeeBiometricIds` mapping table~~ | **Cancelled** — §1 |
| 8 | `BiometricDevices` registry, extra `PunchTypes` | Deferred |

Code changes, for an honest estimate:

- the ingestion endpoint (§4), with server-side date/time derivation (§3) and
  per-record outcomes (§5);
- an `IAsyncAuthorizationFilter` that resolves tenant `DataKey` from the API key and
  stamps it on the request (review D4/D5);
- `EmpCode → EmpId` lookup, tenant-scoped, case-insensitive;
- upsert for the parent `EmployeeAttendance` row (§7.1);
- `ConstructTotalHours`: order by time (§7.2) **and** the out-first crash (review D3);
- admin UI to issue and revoke integration keys.

Bridge-side, the only change to PLAN.md is the watermark rule in §5.

---

## 11. Open questions

1. **Night shift** (§6) — does anyone on these terminals work past midnight? If so
   their hours are already wrong today, and ingestion makes it visible rather than
   causing it.
2. **`EmpCode` data quality** (§1) — the two queries in HRMS-DB-CHANGES.md §2.
3. **Duplicate parent rows** (§7.1) — the preflight query. Determines whether change 5
   needs a data-cleanup migration first.
4. **Does `IQ-1004` match `EmpCode` exactly**, including case and the `IQ-` prefix, or
   is stored `EmpCode` bare (`1004`)? One query against a live tenant settles it; the
   lookup is case-insensitive either way but not prefix-insensitive.
