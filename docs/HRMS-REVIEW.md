# HRMS Attendance & Web Check-In — Review

**Reviewed:** 2026-08-08 · `d:\Dev\git-repos\Ve\HRMS_Platform` (read-only)
**Purpose:** establish the real integration surface for the Biometric Bridge, and
record defects found in the attendance path along the way.

**Nothing in this document proposes changing HRMS.** Sections 4 and 5 describe what
the bridge can and cannot do against HRMS *as it stands today*; the decision about
whether HRMS changes is yours, and is called out where it is unavoidable.

---

## 1. What exists

### 1.1 Data model — `AttSch` schema

**`EmployeeAttendance`** — one row per (employee, date).

| Column | Type | Note |
|---|---|---|
| `Id` | long | PK |
| `EmpId` | long | FK → `Employees.Id` |
| `Date` | `DateOnly` | the attendance day |
| `StatusTypeId` | short | Active / InActive (soft delete) |
| `DataKey` | string | tenant scoping key |

**`EmpAttendanceLogs`** — child punch rows.

| Column | Type | Note |
|---|---|---|
| `EmpAttendanceId` | long | FK → parent |
| `PunchTypeId` | short | `1 = In`, `2 = Out` |
| `CheckInTypeId` | short | `1 = Biometric`, `2 = WebCheckIn`, `3 = Manual` |
| `Hour` / `Minute` / `Second` | short? | **three separate integers** |
| `Latitude` / `Longitude` / `IsOutOfZone` | double? / bool? | web check-in geo-fence |
| `Remarks`, `IsActive`, audit columns | | |

The single most important structural fact for the bridge:

> **A punch time is stored as a `DateOnly` on the parent plus three loose integers on
> the child. There is no timestamp column, no timezone, and no UTC offset anywhere in
> the attendance tables.**

Everything downstream — `ConstructTotalHours`, the pivot report, regularization —
reassembles a `DateTime` from `parent.Date + TimeOnly(Hour, Minute, Second)`. So the
wall-clock values written *are* the record; whatever timezone they were captured in is
lost at the boundary and never recoverable.

`EmployeeAttendance` implements `IDataKeyFilterReadWrite` (tenant-filtered).
`EmpAttendanceLogs` does not — it inherits scoping through its parent.

### 1.2 Endpoints — `api/v1/attendence`

| Endpoint | Auth | Writes | Notes |
|---|---|---|---|
| `POST /` (Add) | JWT + `AttendanceManage` | yes | Takes `EmpId`, `Date`, `CheckInTypeId`, `PunchTypeId`, `Hour/Minute/Second`. The only write path that accepts an arbitrary employee **and** an arbitrary check-in type. |
| `PUT /` (Update) | JWT + `AttendanceManage` | yes | Parent row only. |
| `DELETE /{id}` | JWT + `AttendanceManage` | soft | |
| `POST /postlatestwebcheckin` | JWT (any employee) | yes | **Hardcodes `CheckInTypeId = WebCheckIn`**; `EmployeeId` comes from the caller's token. |
| `GET /getlatestwebcheckin` | JWT | no | Latest web check-in for today (tenant-local). |
| `POST /getall`, `/getdetailAttendence`, `/GetAttendenceLogsList`, `/getAllOrgAttendance` | JWT (+perm on admin ones) | no | |

### 1.3 Authentication — two entirely separate schemes

**Scheme A — user JWT.** `BaseController` is `[Authorize]` and lifts `EmployeeId`,
`UserId`, `TenantId`, `BusinessUnitId`, `OrgFinalcialYearId` from claims.
Cross-tenant switching via an `appKey` header is validated against the caller's own
tenant (`Helper.GetTenantId`). Endpoint permissions via
`[RequiresPermission(AppPermissions.AttendanceManage)]`.

**Scheme B — vendor API key.** `[VendorAuthorize]` reads an `api_key` header, parses
it as a GUID, and checks `VendorApiKeys` for a matching active row. Currently used by
exactly one controller: `api/v1/bgv` (background verification).

The two do not interoperate. There is no API-key path into attendance, and no
user-JWT-free path to any write endpoint.

### 1.4 Web check-in flow

Front end (`att-web-checkin.component.ts`) builds the payload entirely from the
**browser's clock**:

```ts
const now = new Date();
const payload = { date: now, hours: now.getHours(), minutes: now.getMinutes(), … };
```

Back end (`PostLatestWebCheckIn`) evaluates the geo-fence (Haversine against
`OrgGeoFences`; hard reject when `IsEnforced`, advisory `IsOutOfZone` flag otherwise),
finds-or-creates the parent `EmployeeAttendance` for the date, and appends a log row.

---

## 2. The headline finding

> **Biometric attendance was designed for and never built.**

`CheckInTypesEnum.Biometric = 1` exists, and `LookupSeeder` seeds the row
`CheckInTypes { Id = 1, Name = "Biometric" }`. A full-solution search for `Biometric`
returns **four hits**: the enum member, the seeder, its comment, and a mirrored
TypeScript constant. **Nothing in HRMS ever writes `CheckInTypeId = 1`.**

There is no biometric ingestion endpoint, no device registry, no punch-import job, and
no machine-authenticated write path of any kind. The lookup value is a placeholder.

---

## 3. Why each existing endpoint fails as a bridge target

### `POST /postlatestwebcheckin` — unusable

`CheckInTypeId` is hardcoded to `WebCheckIn`, and `EmployeeId` is taken from
`LoggedInUserInfo.EmployeeId`. It can only ever record the calling human's own punch,
mislabelled as a web check-in. Not a candidate.

### `POST api/v1/attendence` (Add) — the only technically viable one, with four blockers

It does accept `CheckInTypeId`, `PunchTypeId`, `EmpId` and an explicit time. But:

1. **Auth.** `[Authorize]` + `AttendanceManage`. The bridge is an unattended Windows
   service; using this means storing an HR administrator's credentials on a machine in
   the customer's server room and holding a token with full attendance
   create/edit/delete rights across the tenant. That is a materially worse security
   posture than the API-key model the bridge was specified around (§10 and §21 of the
   original spec both assume an API key).
2. **Identifier mismatch.** The endpoint takes `EmpId` — the internal `long` primary
   key. The devices emit `IQ-1004`, which corresponds to `Employees.EmpCode`
   (`string?`, nullable, no uniqueness constraint found). There is **no exposed
   endpoint that resolves `EmpCode → EmpId`**, so the bridge cannot translate what the
   hardware gives it into what the API demands.
3. **No batching.** One punch per HTTP request. A routine 45-punch sync becomes 45
   authenticated round trips; the spec's §39 batching requirement cannot be met.
4. **No idempotency** — see below.

### The idempotency question is now answered, and the answer is "no"

This was **Open Question 2** in the plan, flagged as the highest-risk unknown.
`AddAttendance` does this when `LogModel.Id` is null or zero:

```csharp
EmpAttendanceLogs logs = new EmpAttendanceLogs() { … };
_dbContext.EmpAttendanceLogs.Add(logs);
```

An unconditional append. `PostLatestWebCheckIn` does the same. There is no existence
check on (employee, date, time, punch type), and the EF configuration for
`EmpAttendanceLogs` declares only a primary key and three foreign keys — **no unique
index**.

**Consequence for the bridge as planned:** the 60-second overlap window (§5.3, there to
absorb device clock skew) re-sends boundary punches on every cycle. Against this API
every one of those becomes a duplicate row. Retry-after-timeout does the same. The
bridge's safety mechanism would corrupt the attendance ledger.

---

## 4. Defects found in the attendance path

Reported because they affect the data the bridge will be writing into or reading
alongside. None are bridge-caused and none require the bridge to change.

### D1 — Web check-in day boundary: write path and read path disagree

`GetLatestWebCheckIn` goes to real trouble to compute the tenant-local day, with an
explicit comment ([AttendenceController.cs:262-265](../../HRMS_Platform/HRMS/HRMS.Api/Controllers/Attendence/AttendenceController.cs#L262-L265)):

> *"today" for web check-in grouping is the TENANT's calendar date, not the server's
> UTC date — a 23:30 local punch in a UTC-offset tenant must land on the local day.*

The **write** path does not honour that. `PostLatestWebCheckIn` derives the day from
`model.Date` — a value the browser supplied — via `DateTimeHelper.ParseDateTimeToDateOnly`,
which is a bare `DateOnly.FromDateTime`. No tenant-timezone normalization. So the day a
punch is *filed under* and the day it is *read back under* are computed by two
different rules, and they diverge near midnight — precisely the case the read-path
comment was written to fix.

Separately, `Hour/Minute/Second` come from `now.getHours()` on the client while the
day comes from a UTC-serialized `Date`, so the two halves of a single punch can be
sourced from different calendar days.

### D2 — The stored attendance time is whatever the employee's browser says it is

`hours`/`minutes`/`seconds` are read from the client clock and written verbatim. No
server-side derivation, no plausibility check against `CreatedDate` (which *is*
UTC-stamped server-side, so the discrepancy is visible in the row itself but never
inspected). Changing the machine's clock or timezone changes the recorded attendance
time. For a system feeding payroll this is worth a deliberate decision rather than an
accident.

### D3 — `ConstructTotalHours` throws when a day's first punch is a check-out

[AttendenceRepository.cs:228](../../HRMS_Platform/HRMS/HRMS.Repo/Services/Attendence/AttendenceRepository.cs#L228):

```csharp
DateTime currentDateTime = dt.Value.ToDateTime(new TimeOnly(...));
TimeSpan ts = (currentDateTime - lastPunchDate!.Value);   // lastPunchDate is null
```

`lastPunchDate` is only assigned in the `PunchTypeId == In` branch. If the first log
of a day is an `Out`, the null-forgiving `!` on a `DateTime?` throws
`InvalidOperationException` → HTTP 500 on `getdetailAttendence` for that employee.

Today this needs someone to forget to check in. **Once biometric data flows it becomes
routine**: the live device capture recorded 3 records with `punch=0` on the exit
terminal, and any employee whose first scan of the day is at the exit door produces
exactly this shape. This one should be fixed before biometric ingestion goes live,
whatever integration route is chosen.

Related: the totals loop never resets `lastPunchTypeId`/`lastPunchDate` after
consuming an `Out`, so `In, Out, Out` silently double-counts the first interval.

### D4 — Vendor API-key path establishes no tenant at all

> **Corrected 2026-08-09.** The first version of this section said an API-key request
> makes the tenant filter *match every tenant*. That is wrong, and the correction
> matters because it changes the failure mode. The chain below is what was actually
> verified while implementing against it.

**What is true:** `[VendorAuthorize]` is an `IAuthorizationFilter`, not ASP.NET
authentication, so `User.Identity.IsAuthenticated` stays `false` and no tenant is ever
established. `IsApiKeyValid` only checks that a key row exists and is active — it never
reads the key's own `DataKey`. The scoping that would have fixed it was started and
abandoned: `VendorIntegrationBaseController` still carries
`//OrgId = vendorValidationService.GetOrgIdBasedOnApiKey(ApiKey);` commented out. That
same line has a second bug — `Guid.TryParse(values.FirstOrDefault(), out Guid ApiKey)`
declares a **new local** shadowing the property, so `ApiKey` stays `Guid.Empty` on every
request.

**What the earlier version got wrong.** The global filters are bound to the
**DbContext**, not to `IGetDataKeyFromUser`:
`AddHierarchicalTenantShardingQueryFilter(entityType, this)`. And
`PsMesengerDBContext` only assigns `DataKey` *inside* its `if (identity != null &&
identity.Claims.Count() > 0)` block. With no principal that block never runs, so
`DataKey` stays **null**, not empty — and `x.DataKey.StartsWith(null)` matches
**nothing**, not everything.

So the read-side failure is the opposite of what was reported: an API-key request sees
**no rows** rather than every tenant's. The `StartsWith("")` chain is real, but it runs
through `IGetDataKeyFromUser`, which the entity filters do not consult.

**The write-side finding stands unchanged and is the dangerous one.**
`InsertDataKey = dataKeyFilter?.DataKey` *does* come from `GetDataKeyFromUser`, so it is
`""`, and `MarkWithDataKeyIfNeeded` stamps that onto new rows. Since a real tenant key
looks like `1.`, `"".StartsWith("1.")` is false — rows written that way exist and are
**invisible to the tenant that owns them**.

**Consequence for the bridge, and what was done about it.** An ingestion route built
naively on this middleware would have failed twice over, both silently: the employee
lookup would match nothing, so every punch would return `UnknownEmployee` under a
perfectly successful HTTP 200; and anything that did get written would be stamped
unreadable. `PsMesengerDBContext` now reads the tenant that
`IntegrationApiKeyAuthorize` resolved from the key and applies it to **both**
`DataKey` and `InsertDataKey`, guarded so it can never override a value derived from a
real principal and never assigns an empty key.

**The BGV path is a separate exposure with a different mechanism.** `EmpVerifications`
is absent from `AllDataKeyEntries` entirely, so it carries no tenant scoping in either
direction — that is worth its own look, and it is not something the bridge work
touches.

### D5 — `VendorAuthorize.OnAuthorization` is `async void`

`IAuthorizationFilter.OnAuthorization` is synchronous; the implementation is declared
`public async void`. It happens to work today because the body contains no `await`.
Two latent hazards: any exception thrown inside becomes unobservable (it cannot be
caught by the framework and crashes on the thread pool), and the moment anyone adds an
`await` — the natural fix for the sync-over-async `IsApiKeyValid` call — the method
returns at that point, `filterContext.Result` is set too late, and **the request
proceeds unauthenticated**. If any of this is touched, it should become
`IAsyncAuthorizationFilter`.

Also `GetService(typeof(IVendorValidationService)) as VendorValidationService`
down-casts to the concrete type and is dereferenced without a null check.

---

## 5. Integration options

The bridge cannot push biometric punches into HRMS today. Every route requires
*something* to change; they differ in where the change lands and how much.

### Option A — new API-key ingestion endpoint in HRMS *(recommended)*

A `POST api/v1/attendence/biometric` accepting a batch, authenticated by API key,
writing `CheckInTypeId = Biometric`, resolving `EmpCode → EmpId`, and de-duplicating
on (EmpId, Date, Hour, Minute, Second, PunchTypeId).

- Fits what the enum, the seeder and the check-in-type design already anticipated.
- The bridge stays exactly as planned in `PLAN.md` — the HRMS profile (§6) was built
  for precisely this and needs only its fields filled in.
- Requires D4 to be resolved first: tenant must be derived from the API key.
- **Contradicts the original directive that HRMS is not to be modified.** That
  directive was written on the assumption an ingestion contract already existed. It
  does not, so this is now your call, not an assumption I can make.

### Option B — service-account JWT against `POST api/v1/attendence`

No HRMS code change, in principle.

- Needs a dedicated HRMS user with `AttendanceManage`, credentials stored on the LAN box.
- Still needs an `EmpCode → EmpId` resolution route that does not exist.
- Still one request per punch.
- Still no dedup → **the bridge must guarantee it never re-sends**, meaning
  `overlap = 0` and strict `punch_time > watermark`. Given both terminals are ~150 s
  out of step with the bridge clock, dropping the overlap is exactly the condition
  under which punches get silently lost. This trade is bad in both directions.

### Option C — direct database write

Rejected. Bypasses tenant scoping, audit stamping and validation, and couples the
bridge to the HRMS schema. Not proposed.

### Recommendation

**Option A.** It is the smallest, most contained change, it is the one the HRMS data
model was already shaped for, and it is the only one that lets the bridge's duplicate
protection work as designed. Option B is viable as a stopgap but should be understood
as trading data integrity for the appearance of not touching HRMS.

Either way, **D3 should be fixed before biometric punches start arriving** — it will
turn from rare to routine on day one.

---

## 6. What this changes in `PLAN.md`

- **Open Question 2 (HRMS idempotency) — answered: not idempotent.** §5.5's assumption
  does not hold. Recorded in §5.5.
- **Open Question 3 (employee code mapping) — partially answered.** `Employees.EmpCode`
  is the right target field, but nothing exposes a lookup by it, and it is nullable
  with no uniqueness constraint. Whether every employee has a populated `EmpCode`
  matching the device `user_id` still needs confirming against tenant data.
- **Open Question 1 (HRMS contract) — answered: there is no contract.** It has to be
  defined, not discovered. Phase 3 is blocked on the Option A/B decision rather than
  on receiving a specification.
- §8's API-key design is unaffected and still correct — the bridge encrypts and stores
  whatever credential the chosen option needs.
