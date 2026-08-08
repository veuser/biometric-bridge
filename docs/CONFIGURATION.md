# Configuration

Settings live in two places, split by whether they must be known before the database
exists.

| Where | What | Changed by |
|---|---|---|
| Environment (`deployment/winsw/BiometricBridge.xml`) | Host, port, data root, log level | Editing the service XML, then restart |
| Database, via the portal | Everything about synchronization behaviour | **Settings** page, effective immediately |

The HRMS API key is in neither: it is entered in the portal and stored in the
database, so it never appears in a config file or a process listing.

---

## Environment variables

All prefixed `BIOMETRIC_BRIDGE_`.

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `production` | |
| `HOST` | `0.0.0.0` | Binds all interfaces so the portal is reachable from the LAN. Exposure is limited by the firewall rule, not by this. |
| `PORT` | `8080` | Must match the firewall rule |
| `PORTAL_TLS_ENABLED` | `false` | Set to `true` once a certificate is in place — it turns on the `Secure` cookie flag and HSTS |
| `DEFAULT_TIMEZONE` | `Asia/Kolkata` | How device-local timestamps are interpreted |
| `LOG_LEVEL` | `INFO` | `DEBUG` is verbose; useful when diagnosing a device |
| `LOG_MAX_BYTES` | `10485760` | Rotation threshold |
| `LOG_BACKUP_COUNT` | `10` | |
| `DATA_ROOT` | `%ProgramData%\BiometricBridge` | Rarely changed |

---

## Sync settings (portal)

### Automatic synchronization

| Setting | Default | What it affects |
|---|---|---|
| Run automatically | Off | Master switch. Off means nothing is pushed. |
| Interval | 5 min | How often every enabled device is polled |
| Overlap | 60 s | How far back past the watermark each window reaches |
| First-sync lookback | 24 h | How far back a brand-new device reaches on its first run |

**Why 5 minutes and not 1.** The ZK protocol has no server-side time filter, so every
poll transfers each terminal's entire buffer — measured at ~7.5 s for ~20,000 records
per device. At 1 minute with two devices the bridge would spend a quarter of its life
reading. The portal warns when the interval leaves little headroom.

**What overlap is actually for.** Not clock skew — the clock guards handle that. It
catches records written into the device buffer *after* a read but timestamped
*before* it finished, which would otherwise fall between two windows. The cost is
that boundary records are re-read every cycle; an in-memory suppression cache stops
them being re-*sent*.

**First-sync lookback is a safeguard.** Set it deliberately before enabling sync. A
terminal holding two years of history will otherwise push all of it into HRMS on the
first run.

### Time and device clocks

| Setting | Default | What it affects |
|---|---|---|
| Timezone | `Asia/Kolkata` | Interpretation of device-local timestamps |
| Drift warning | 120 s | Threshold for the dashboard warning |
| Max clock movement | 300 s | Sync is **refused** if a device clock moves more than this between runs |

The last one is a data-loss guard, not a nicety. If a terminal's clock jumps forward,
the watermark leaps with it and every record in the skipped interval is never
collected. Refusing and telling the operator is better than losing a morning of
attendance silently. A backwards jump is refused too — it would invert the window and
return nothing indefinitely.

Recovery from either is: correct the clock on the terminal, then run a **manual
range** sync over the affected period.

### Limits and retention

| Setting | Default | What it affects |
|---|---|---|
| Max manual window | 31 days | Ceiling on an operator-chosen range |
| Stale lock timeout | 30 min | When a lock left by a crashed process is reclaimed |
| History retention | 180 days | How long sync-run records are kept |

Retention applies to *run metadata* only. There is no punch data to expire, because
none is stored.

---

## Device settings

| Field | Notes |
|---|---|
| Punch type | `CHECK_IN` / `CHECK_OUT` for dedicated terminals, `FROM_DEVICE` for mixed |
| Communication key | Numeric ZK comm key; blank on edit keeps the stored one |
| Timeout | Per-device; raise it for a slow or heavily loaded terminal |
| Force UDP | Only if the terminal does not accept TCP |
| Timezone override | For a multi-site deployment spanning timezones |

**Punch type is worth getting right.** On a dedicated entry/exit pair, the device the
punch came from is authoritative — the reference exit terminal was observed reporting
a handful of check-in-coded records because somebody pressed the wrong key. The role
override makes that harmless. Use `FROM_DEVICE` only where one terminal genuinely
handles both directions.

---

## HRMS configuration

Covered in [HRMS-PROFILE.md](HRMS-PROFILE.md). In brief: base URL, API key, an
optional tenant identifier, and a JSON profile describing your API.

The API key is write-only. It is never returned by any endpoint, never rendered in
the portal, and is scrubbed from the log file. Leaving the field blank on an edit
keeps the stored key.

Plain `http://` is refused unless *allow insecure HTTP* is ticked, because the key
would otherwise cross the network in clear text.
