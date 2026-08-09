# HRMS Database Changes to Support Biometric Attendance

**Date:** 2026-08-08 · scopes **Option A** from [HRMS-REVIEW.md](HRMS-REVIEW.md) §5
**Target:** PostgreSQL (Npgsql), EF Core, `HRMS.DBModels/Migrations`

These are changes to **HRMS**, which the original brief forbade. The brief assumed an
ingestion contract already existed; it does not. This document scopes what Option A
actually costs so the decision can be made on facts. Nothing here has been implemented.

**Design constraint throughout:** every change is additive and every new index is
*partial*, scoped to rows that do not exist yet. No existing row is rewritten, no
existing query plan changes, and every migration builds against an empty set. That is
deliberate — it makes the whole set rollback-safe on live tenant databases.

---

## Summary

| # | Change | Table | Necessity |
|---|---|---|---|
| 1 | `SourceRef`, `DeviceSerialNumber`, `PunchTimeUtc` columns | `AttSch.EmpAttendanceLogs` | **Required** |
| 2 | Partial unique index on the biometric natural key | `AttSch.EmpAttendanceLogs` | **Required** |
| 3 | Unique index on `(DataKey, lower(EmpCode))` | `EmpSch.Employees` | **Required** |
| 4 | New `IntegrationApiKeys` table | `BaseSch` | **Required** |
| 5 | Unique index on `(EmpId, Date)` | `AttSch.EmployeeAttendance` | **Required** |
| 6 | Fix inverted `CheckInTypes` seed (ids 2/3) | `BaseSch.CheckInTypes` | Strongly recommended |
| 7 | ~~`EmployeeBiometricIds` mapping table~~ | `AttSch` | **Cancelled** — see §3 |
| 8 | `BiometricDevices` registry | `AttSch` | **Not recommended now** |
| 9 | Extra `PunchTypes` rows (break/OT) | `BaseSch.PunchTypes` | Not now |

Five required changes, one strongly recommended, two deliberately deferred.

**Updated 2026-08-08**, after the customer confirmed the device `user_id` field is
`Employees.EmpCode`: change 7 is cancelled, and change 5 was added — it is a
prerequisite for change 2 to actually work. Both are explained in
[ATTENDANCE-INGESTION.md](ATTENDANCE-INGESTION.md), §1 and §7.1 respectively.

---

## 1. `AttSch.EmpAttendanceLogs` — provenance and idempotency

### 1.1 New columns

```csharp
migrationBuilder.AddColumn<string>(
    name: "SourceRef", schema: "AttSch", table: "EmpAttendanceLogs",
    type: "character varying(64)", maxLength: 64, nullable: true);

migrationBuilder.AddColumn<string>(
    name: "DeviceSerialNumber", schema: "AttSch", table: "EmpAttendanceLogs",
    type: "character varying(64)", maxLength: 64, nullable: true);

migrationBuilder.AddColumn<DateTime>(
    name: "PunchTimeUtc", schema: "AttSch", table: "EmpAttendanceLogs",
    type: "timestamp with time zone", nullable: true);
```

All three nullable, so every existing row and every future web/manual check-in is
untouched.

- **`SourceRef`** — the bridge's deterministic `client_record_id`
  (`sha256(device_serial | employee_code | punch_time_utc | raw_punch)`, truncated).
  Stable across retries: the same physical punch always produces the same value, which
  is what makes a timed-out-but-actually-committed request safe to replay.
- **`DeviceSerialNumber`** — which terminal recorded it. Plain string, no FK (see §7).
  Needed for support ("this punch came from the exit door") and for the entry/exit
  role attribution to be auditable after the fact.
- **`PunchTimeUtc`** — the exact instant, preserved.

### 1.2 Why `PunchTimeUtc` is worth the column

The existing model stores a punch as `EmployeeAttendance.Date` (a `DateOnly`) plus
`Hour`/`Minute`/`Second` as three separate `smallint`s. There is no timezone and no
offset anywhere in the attendance tables, so the wall-clock digits *are* the record and
the instant they represent is unrecoverable.

The bridge works in UTC internally (it has to — the terminals' clocks are ~150 s out of
step with each other and with the bridge). Writing into HRMS means converting back to
tenant-local wall clock and splitting into three integers, discarding the offset.

`PunchTimeUtc` keeps the original instant alongside the legacy triple:

- Existing readers (`ConstructTotalHours`, the pivot report, regularization) keep using
  `Hour`/`Minute`/`Second` and are completely unaffected.
- Reconciliation between the bridge and HRMS becomes exact rather than inferential —
  when a punch is disputed, there is one authoritative value to compare.
- It gives a migration path for defects D1/D2 in the review (browser-supplied wall
  clock, and the read/write day-boundary disagreement) without another schema change
  later.

Dual-write, one nullable column. Cheap insurance against the one structural weakness
in the attendance schema.

### 1.3 The dedup index — the change that makes ingestion safe

This is the change everything else depends on. Today `AddAttendance` and
`PostLatestWebCheckIn` both append a log row unconditionally, and the EF configuration
for `EmpAttendanceLogs` declares a primary key and three foreign keys and nothing else.
Nothing deduplicates.

```sql
CREATE UNIQUE INDEX "UX_EmpAttendanceLogs_BiometricPunch"
    ON "AttSch"."EmpAttendanceLogs"
       ("EmpAttendanceId", "PunchTypeId", "Hour", "Minute", "Second")
    WHERE "CheckInTypeId" = 1;
```

The ingestion endpoint then writes with `ON CONFLICT DO NOTHING` against this index (or
EF's equivalent), and re-sending a punch is a no-op rather than a duplicate row.

**Why the natural key and not `SourceRef`:** the natural key also catches the same
physical punch arriving by a different route — a bridge reinstall that changes the hash
input, a manual recovery sync overlapping a scheduled one, two devices that both saw
the person. `SourceRef` only catches exact replays of the same computed value. Index
`SourceRef` too (non-unique) for tracing, but let the natural key be the constraint.

**Why `WHERE "CheckInTypeId" = 1`:**

1. Nothing in HRMS currently writes `CheckInTypeId = 1` — verified, four references
   solution-wide, all declarations. **So this index builds over zero rows.** No
   duplicate-cleanup migration, no risk of failing on live tenant data, no lock of
   consequence. That is the whole reason to scope it this way.
2. Web check-in and manual entry are left alone. A legitimate duplicate there (someone
   pressing check-in twice in the same second) keeps behaving exactly as it does today.
   Changing that is a separate decision with its own blast radius.

**Tenant safety:** `EmpAttendanceLogs` is not `IDataKeyFilterReadWrite` — it is scoped
through its parent `EmployeeAttendance`, which is. Since `EmpAttendanceId` is in the
index, two tenants can never collide.

---

## 2. `EmpSch.Employees` — making `EmpCode` resolvable

The devices emit `IQ-1004`. The existing write endpoint takes `EmpId`, the internal
`long` PK. The bridge cannot translate one into the other, and nothing in HRMS exposes
a lookup.

`Employees.EmpCode` is `string?` with **no index and no uniqueness constraint**. Before
it can be an integration key it needs both.

```sql
CREATE UNIQUE INDEX "UX_Employees_DataKey_EmpCode"
    ON "EmpSch"."Employees" ("DataKey", lower("EmpCode"))
    WHERE "EmpCode" IS NOT NULL AND "EmpCode" <> '';
```

- **Scoped by `DataKey`** because `Employees` is `IDataKeyFilterReadWrite` and codes
  only need to be unique within a tenant.
- **`lower(...)`** because PostgreSQL is case-sensitive and no one should be able to
  create both `IQ-1004` and `iq-1004`. The lookup must use the same expression to hit
  the index.
- **Partial** so the many employees with no code are unaffected.

### Run this before committing to the approach

Unlike everything else here, **this index can fail on existing data.** Two employees in
one tenant sharing a code, or differing only in case, will block it:

```sql
SELECT "DataKey", lower("EmpCode") AS code, count(*), array_agg("Id")
FROM   "EmpSch"."Employees"
WHERE  "EmpCode" IS NOT NULL AND "EmpCode" <> ''
GROUP  BY 1, 2
HAVING count(*) > 1;
```

And to size the mapping question in §3:

```sql
SELECT count(*) FILTER (WHERE "EmpCode" IS NULL OR "EmpCode" = '') AS missing_code,
       count(*) AS total
FROM   "EmpSch"."Employees";
```

Both answers change what gets built. The first decides whether a data-cleanup migration
is needed; the second decides §3.

---

## 3. Employee ↔ device-user mapping — CANCELLED

**Resolved 2026-08-08: the customer confirmed the device `user_id` field is
`Employees.EmpCode`.** The live capture shows `IQ-1004` → Manoj Settebathula, and
`IQ-1004` is the employee code.

So resolution is a direct tenant-scoped, case-insensitive lookup on the index in §2,
and the `EmployeeBiometricIds` table proposed here is not needed. One fewer table, one
fewer admin screen, and no registration in `AllDataKeyEntries` to get wrong.

The §2 queries still matter — they establish that every enrolled employee actually has
a non-empty, unique `EmpCode`. A device user with no matching code produces punches
that cannot be attributed; that is handled as a **terminal** per-record failure rather
than a batch failure, per [ATTENDANCE-INGESTION.md](ATTENDANCE-INGESTION.md) §5.

---

## 3a. `AttSch.EmployeeAttendance` — unique parent per employee per day

**Required, and a prerequisite for §1.3 to mean anything.**

`EmployeeAttendance` carries plain indexes on `DataKey`, `EmpId` and `StatusTypeId`, and
**no unique constraint on `(EmpId, Date)`**. Both write paths find-then-create
(`GetEmpAttandance` → `FirstOrDefaultAsync` → `Add`), which races under concurrency.

That defeats the child index in §1.3. It keys on
`(EmpAttendanceId, PunchTypeId, Hour, Minute, Second)` — so if two parent rows exist for
one employee on one day, the same punch inserts cleanly under each, because
`EmpAttendanceId` differs. **The parent has to be unique or the child constraint is
decorative.**

```sql
CREATE UNIQUE INDEX "UX_EmployeeAttendance_Emp_Date_Active"
    ON "AttSch"."EmployeeAttendance" ("EmpId", "Date")
    WHERE "StatusTypeId" = 1;
```

Unlike §1.3, this index covers **rows that already exist**, so it is the second of the
two changes here that can fail on live data. Preflight:

```sql
SELECT "DataKey", "EmpId", "Date", count(*), array_agg("Id")
FROM   "AttSch"."EmployeeAttendance"
WHERE  "StatusTypeId" = 1
GROUP  BY 1, 2, 3
HAVING count(*) > 1;
```

Rows returned need merging — re-point their child logs at one survivor, deactivate the
rest — before the index can be created. Ingestion should also upsert
(`ON CONFLICT DO NOTHING`, then re-read) rather than find-then-create, or the race just
becomes an exception under load instead of a duplicate row.

---

## 4. `BaseSch.IntegrationApiKeys` — machine authentication

The bridge is an unattended Windows service. It needs an API key, not a user JWT.

### Why not reuse `VendorApiKeys`

That table stores the key as a **plaintext `uuid`**, has no expiry, no revocation
timestamp, no scope and no last-used tracking. `IsApiKeyValid` checks only that a row
exists and is active — it never reads the key's own `DataKey`, which is defect D4 in
the review. It also backs a live BGV integration; widening it means changing an
authentication path that is already in production for a different consumer.

A separate table keeps the blast radius at zero and lets the design be right.

```csharp
public partial class IntegrationApiKey
{
    public long     Id { get; set; }
    public string   Name { get; set; } = null!;          // "Hyderabad Biometric Bridge"
    public string   KeyPrefix { get; set; } = null!;     // varchar(12), non-secret, for display/lookup
    public string   KeyHash { get; set; } = null!;       // varchar(128) — SHA-256/Argon2 of the secret
    public string   Scope { get; set; } = null!;         // "attendance.write"
    public string   DataKey { get; set; } = null!;       // the OWNING tenant — resolved FROM the key
    public bool     IsActive { get; set; }
    public DateTime? ExpiresAt { get; set; }             // timestamptz
    public DateTime? LastUsedAt { get; set; }
    public DateTime? RevokedAt { get; set; }
    public Guid?    CreatedBy { get; set; }
    public DateTime? CreatedDate { get; set; }
}
```

```sql
CREATE UNIQUE INDEX "UX_IntegrationApiKeys_KeyHash" ON "BaseSch"."IntegrationApiKeys" ("KeyHash");
CREATE INDEX        "IX_IntegrationApiKeys_KeyPrefix" ON "BaseSch"."IntegrationApiKeys" ("KeyPrefix");
```

The secret is shown once at creation and never again — which matches what the bridge
already does at its end (§8 of PLAN.md encrypts it at rest via DPAPI and never returns
it through any API or log).

### The critical design note

> **`IntegrationApiKeys` must NOT be registered in `AllDataKeyEntries.cs`, and this is
> the opposite of the rule for every other new tenant table.**

The global filter is `x.DataKey.StartsWith(dataKey.DataKey)`, and `dataKey.DataKey` is
resolved from the authenticated user. At the moment this table is read there is no
authenticated user yet — the whole point of the read is to *discover* which tenant the
caller is. Registering it creates a circular dependency; the lookup must run with
`IgnoreQueryFilters()` and then **set** the request's `DataKey` from the row it found.

Both failure modes are already visible in the codebase, which is why this is worth
stating explicitly:

- **Filtered when it shouldn't be** — `VendorApiKeys` *is* registered, so a lookup that
  needed the tenant in order to find the tenant would depend on its own result.
- **Not filtered when it should be** — `OrgGeoFence` has a `DataKey` column but is
  **absent** from `AllDataKeyEntries`. So `PostLatestWebCheckIn`'s
  `_dbContext.OrgGeoFences.FirstOrDefaultAsync(x => x.IsActive && x.IsEnforced)`
  matches *any tenant's* geo-fence. One tenant enabling an enforced fence can block
  check-in for another. A live bug, found while scoping this work.

**Resolving the tenant from the key is not optional, and skipping it fails silently in
two directions at once.** Both verified during implementation:

- The global filters bind to the **DbContext**, and `PsMesengerDBContext` assigns
  `DataKey` only inside its `if (identity != null && ...)` block. With no principal it
  stays **null**, and `StartsWith(null)` matches **nothing** — the employee lookup
  returns no rows and every punch comes back `UnknownEmployee` under a successful 200.
- `InsertDataKey` does come from `GetDataKeyFromUser`, so it is `""`, and
  `MarkWithDataKeyIfNeeded` stamps that onto new rows. A real tenant key looks like
  `1.`, so `"".StartsWith("1.")` is false — anything written is **invisible to the
  tenant that owns it**.

Neither raises an error. The implemented fix applies the key's tenant to **both**
properties, guarded so a value from a real principal always wins and an empty key is
never assigned.

---

## 5. `CheckInTypes` — the seed contradicts the enum

Found while scoping this. `HRMS.Common/Enums/StatusTypesEnum.cs`:

```csharp
public enum CheckInTypesEnum { Biometric = 1, WebCheckIn = 2, Manual = 3 }
```

`HRMS.DBModels/SeedData/LookupSeeder.cs`, under a comment that restates the enum
correctly:

```csharp
// CheckInTypesEnum: Biometric=1, WebCheckIn=2, Manual=3
new CheckInTypes { Id = 1, Name = "Biometric",    IsActive = true },
new CheckInTypes { Id = 2, Name = "Manual",       IsActive = true },   // enum says WebCheckIn
new CheckInTypes { Id = 3, Name = "Web Check In", IsActive = true },   // enum says Manual
```

**Ids 2 and 3 are swapped.** `PostLatestWebCheckIn` writes
`CheckInTypeId = (short)CheckInTypesEnum.WebCheckIn` = 2, and
`GetDetailLogForEmployee` renders `CheckTypeName = ct.Name` from the joined row — so
**every web check-in in the system currently displays as "Manual"**, and every manual
entry displays as "Web Check In".

Totals are unaffected: `TotalHoursWebCheckIn` filters on the enum value, and both the
writer and the filter use the enum, so they agree with each other. Only the
user-visible label is wrong.

This is the same defect the same file already documents for `LeaveStatusTypes` three
methods below — *"this seed used to invert ids 3/4 … realigned by the
FixLeaveStatusTypeInversion data migration"*. Same bug, different lookup, not yet
caught. The fix follows that established precedent: correct the seed and add a data
migration that swaps the two names on existing tenants.

**Biometric = 1 is consistent in both places**, so ingestion is not blocked by this.
But the fix touches the exact lookup table biometric attendance is about to start
writing, so it belongs in this batch rather than after it.

---

## 6. Deliberately not doing

### `BiometricDevices` registry table — not now

Tempting for FK integrity on `DeviceSerialNumber` and for an admin device list. Skipped
because the bridge already owns device configuration (name, IP, port, serial, location,
role, enabled), and a second copy in HRMS creates two sources of truth that will
disagree the first time someone edits one. `DeviceSerialNumber` as a plain string
carries the provenance that reports actually need.

Add it later only if HRMS needs to render friendly device names, or to reject punches
from unregistered serials. Neither is a day-one requirement, and the string column
already in §1 is what such a table would join to.

### Extra `PunchTypes` rows — not now

Seeded rows are `In = 1`, `Out = 2`. The terminals report six states (check-in,
check-out, break-out, break-in, OT-in, OT-out) and the live capture confirms the
firmware sets them. The bridge's per-device `role` maps everything to In/Out, which is
correct for a dedicated entry/exit pair, so no change is needed.

If `FROM_DEVICE` mode is ever enabled for a mixed-use terminal, this needs four more
seeded rows, four more `PunchInTypesEnum` members, and — importantly — a review of
`ConstructTotalHours`, which pairs punches by assuming only In and Out exist.

---

## 7. Migration sequence

Following the house convention: hand-written, `yyyyMMddHHmmss_PascalCase.cs`, explicit
`[DbContext]` + `[Migration]` attributes, XML doc explaining *why* (as in
`20260807130000_DropUserTempPassword`). Hand-written rather than scaffolded because
other work is in flight against this model and `migrations add` would sweep it in.

| Order | Migration | Risk |
|---|---|---|
| 1 | `AddIntegrationApiKeys` | None — new table |
| 2 | `AddBiometricProvenanceToAttendanceLogs` | None — three nullable columns |
| 3 | `AddBiometricPunchUniqueIndex` | None — partial index over zero rows |
| 4 | `AddEmployeeCodeUniqueIndex` | **Can fail on existing data — run §2 queries first** |
| 5 | `AddEmployeeAttendanceDayUniqueIndex` | **Can fail on existing data — run §3a query first** |
| 6 | `FixCheckInTypeSeedInversion` | Low — data migration, 2 rows per tenant |

Migrations 1–3 are unconditional and carry no data risk. Migrations 4 and 5 are the two
that touch existing rows, and the preflight queries in §2 and §3a tell you in advance
whether they will succeed. Migration 6 is independent of the rest and can ship
separately.

Migration 5 must land **before** ingestion is enabled, not merely before go-live —
without it migration 3's index does not actually prevent duplicates (§3a).

Also required, and not schema: `AllDataKeyEntries.cs` deliberately **does not** gain
`IntegrationApiKeys` (§4). With §3 cancelled, nothing is added to that file.

---

## 8. What this does not cover

Code-side work that Option A also needs, listed so the estimate is honest:

- The ingestion endpoint itself (batch accept, `ON CONFLICT DO NOTHING`, per-record
  result reporting so the bridge can count success/failure).
- An `IAsyncAuthorizationFilter` for API-key auth that resolves the tenant `DataKey`
  from the key and sets it on the request — replacing the `async void` pattern in
  `VendorAuthorize` (defect D5).
- `EmpCode → EmpId` resolution, tenant-scoped and case-insensitive.
- Admin UI to issue and revoke integration keys.
- Fixing D3 (`ConstructTotalHours` throwing on an out-first day) — **not optional
  once biometric data flows**, since the exit terminal already produces that shape.
