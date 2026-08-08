# Wiring your HRMS API

The Biometric Bridge does not know your HRMS. It knows how to make an HTTP request,
and it reads everything contract-shaped from a **profile** — endpoint, method, auth,
field names, batching, timestamp format, and how to read the response.

That means connecting a real HRMS is a configuration change, not a code change, and
the bridge never sends a field your API did not ask for.

Until a real `attendance_path` is set, the bridge **refuses to enable sync**. It will
not guess an endpoint.

---

## What the bridge has to send

Per punch, the bridge knows exactly this much:

| Bridge field | Example | Notes |
|---|---|---|
| `employee_code` | `IQ-1004` | The device user id, verbatim |
| `punch_time` | `2026-08-08T12:07:09+05:30` | Timezone-aware |
| `punch_type` | `CHECK_IN` / `CHECK_OUT` | From the device's role |
| `verification_type` | `FINGERPRINT` / `UNKNOWN` | How the person identified |
| `device_serial` | `BJ2C184562186` | Optional |
| `device_id` | `2` | Bridge-local id, optional |
| `client_record_id` | `9f2c…` | Stable hash, optional — see idempotency |

**It never sends employee names**, or anything else beyond this list.

---

## Minimum you need to tell us

1. The **path** of the attendance endpoint, and the **HTTP method**.
2. How the **API key** is presented — header name, or `Bearer`, or a query parameter.
3. Whether it takes **one record or a batch**, and if a batch, whether it is a bare
   array or nested under a key.
4. The **field name** your API uses for each field above, and `null` for any you do
   not want.
5. What a **success** looks like — status codes, and any flag in the body.
6. What happens on a **duplicate punch** (see idempotency, below).

---

## Key reference

### `attendance_path`, `method`
Path relative to `base_url`, must start with `/`. Method must be `POST`, `PUT` or
`PATCH` — attendance submission is a write.

### `auth`
```jsonc
{ "scheme": "header", "header_name": "X-API-Key", "value_template": "{api_key}" }
{ "scheme": "bearer" }                                  // Authorization: Bearer <key>
{ "scheme": "query",  "query_param": "api_key" }        // ?api_key=<key>
{ "scheme": "none" }
```
`value_template` covers prefixed schemes without code changes — `"Token {api_key}"`,
`"ApiKey {api_key}"`, and so on.

### `tenant`
Set `header_name` or `query_param` if your API needs a company/tenant identifier.
The value comes from the HRMS Configuration page.

### `batch`
| `mode` | Body shape |
|---|---|
| `array` | `[ {...}, {...} ]` |
| `envelope` | `{ "records": [ {...} ] }` — key from `envelope_path`, dotted paths allowed (`data.items`) |
| `single` | one record per request, `{...}` |

`size` caps records per request. Ignored in `single` mode.

### `field_map`
Your names for our fields. **`null` omits the field entirely** — not sent as null,
because a stray null can fail validation on a strict API.

```jsonc
"field_map": {
  "employee_code": "empId",
  "punch_time": "punchDateTime",
  "punch_type": "direction",
  "verification_type": null,      // not sent at all
  "device_serial": null,
  "device_id": null,
  "client_record_id": null
}
```

### `value_map`
Translates our enum values into your vocabulary. Unmapped values pass through.

```jsonc
"value_map": { "punch_type": { "CHECK_IN": "I", "CHECK_OUT": "O" } }
```

### `datetime_format`, `output_timezone`
| Value | Produces |
|---|---|
| `iso8601_offset` | `2026-08-08T12:07:09+05:30` |
| `iso8601_utc_z` | `2026-08-08T06:37:09Z` |
| `custom` | uses `datetime_pattern`, e.g. `%Y-%m-%d %H:%M:%S` |

Timestamps are held in UTC internally and converted to `output_timezone` on the way
out, so an API expecting local wall-clock time gets it.

### `response`
All optional — most APIs say enough with the status code.

```jsonc
"response": {
  "success_status_codes": [200, 201],
  "success_path": "success",              // 200 with {"success": false} = failure
  "error_message_path": "message",
  "processed_count_path": "data.processed",
  "failed_count_path": "data.failed"
}
```
Paths are dotted: `data.summary.processed`, `results.0.ok`.

### `response.results_path` — partial acceptance
Only needed if your API accepts some records and rejects others in one response:

```jsonc
"response": {
  "results_path": "results",
  "result_id_path": "employeeCode",   // or clientRecordId, if you take that field
  "result_ok_path": "accepted"
}
```
Without these, a batch is all-or-nothing, which is the usual case.

### `test_connection`
```jsonc
{ "path": "/api/health", "method": "GET" }
```
Give us any cheap authenticated endpoint and the portal's **Test HRMS Connection**
button will use it. Leave it `null` and the portal will say connectivity cannot be
verified — the bridge will **not** fabricate a punch to prove it works, because that
would write invented attendance into your production HRMS.

---

## The one thing we need you to confirm: duplicates

The bridge re-sends a small overlap window on every cycle, and retries a batch after
a timeout. Both mean **your endpoint may receive the same punch more than once**
(same employee, same timestamp, same type).

- **If your API deduplicates or accepts it harmlessly** — nothing to do.
- **If it would create a duplicate attendance row** — tell us. We set the overlap to
  zero, which removes the duplicate risk and accepts a small chance of missing a
  record when a device clock drifts.
- **If your API has an idempotency/external-reference field**, map it to
  `client_record_id` and the problem disappears entirely. That value is a stable
  hash of device + employee + timestamp + punch state, identical across retries and
  restarts.

This is the highest-risk unknown in the integration. Everything else is a config key.

---

## Worked example

An API taking `POST /api/v1/attendance/bulk`, key in `X-Api-Token`, records nested
under `data.punches`, local-time strings, and a `status` flag in the body:

```jsonc
{
  "attendance_path": "/api/v1/attendance/bulk",
  "method": "POST",
  "auth": { "scheme": "header", "header_name": "X-Api-Token", "value_template": "{api_key}" },
  "batch": { "mode": "envelope", "envelope_path": "data.punches", "size": 100 },
  "field_map": {
    "employee_code": "empCode",
    "punch_time": "punchTime",
    "punch_type": "type",
    "verification_type": null
  },
  "value_map": { "punch_type": { "CHECK_IN": "IN", "CHECK_OUT": "OUT" } },
  "datetime_format": "custom",
  "datetime_pattern": "%Y-%m-%d %H:%M:%S",
  "output_timezone": "Asia/Kolkata",
  "response": {
    "success_status_codes": [200],
    "success_path": "status",
    "error_message_path": "message",
    "processed_count_path": "data.inserted"
  },
  "test_connection": { "path": "/api/v1/ping", "method": "GET" }
}
```

Produces:

```http
POST /api/v1/attendance/bulk
X-Api-Token: <key>
Content-Type: application/json

{"data": {"punches": [
  {"empCode": "IQ-1004", "punchTime": "2026-08-08 12:07:09", "type": "OUT"}
]}}
```
