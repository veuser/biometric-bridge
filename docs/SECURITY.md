# Security

This document states plainly what is protected, what is not, and why — including the
decisions that were made in favour of simplicity over hardening.

---

## Deliberately not protected

Two decisions were taken by the customer. Both are recorded here rather than left to
be discovered later.

### The portal runs over plain HTTP

There is no TLS on the LAN portal.

**Consequence:** the session cookie and the sign-in password are readable by anyone
who can observe traffic between an operator's browser and this machine. Portal
security is therefore only as strong as the physical and switched network.

**Mitigations in place:** the firewall rule is scoped to the local subnet rather than
Any; sessions have an absolute lifetime as well as an idle timeout, bounding how long
a captured cookie stays useful; and the HRMS API key is never sent to the browser, so
a captured session grants portal access but not the HRMS credential.

**To enable TLS later:** place a certificate on the machine and set
`BIOMETRIC_BRIDGE_PORTAL_TLS_ENABLED=true`. The code reads the `Secure` cookie flag
and HSTS from that setting; no code change is required.

### The HRMS API key is stored unencrypted

Encryption at rest is currently off.

**Consequence:** anyone who can read
`C:\ProgramData\BiometricBridge\data\biometric_bridge.db` — or **any backup of it** —
can recover the HRMS credential. Directory ACLs are the only control.

**Mitigations in place:** the installer removes ACL inheritance and grants only
SYSTEM and Administrators; the key is never returned by any API, never rendered in
the portal, and is actively scrubbed from the log file; only the last four characters
are stored separately, for the masked display.

**To enable encryption later:** every read and write already goes through a
`SecretProtector` interface, and every stored value carries a scheme tag. Adding a
DPAPI implementation is a configuration change plus a one-off re-wrap of existing
rows — no schema migration, and no ambiguity about which rows are already protected.

---

## What is protected

### Portal authentication

- Argon2id password hashing, with transparent re-hashing when cost parameters change.
- **No default credentials.** The first administrator is created through a setup
  endpoint that is only reachable while no user exists. This is the most common way
  appliances of this kind are compromised.
- Opaque server-side session tokens in an httpOnly, SameSite=Strict cookie — not
  JWTs. Logout, disabling a user and changing a password all revoke access on the
  very next request.
- Password changes invalidate every session opened with the old password.
- Login failures return one message for both a wrong password and an unknown user,
  and a missing user is still hashed against, so timing does not enumerate accounts.
- Rate limiting of 5 attempts per 15 minutes, keyed on username **and** address
  together — username alone would let anyone lock an administrator out of their own
  portal. Plus a durable account lockout after 10 consecutive failures.
- Password length is a deployment decision: `BIOMETRIC_BRIDGE_MIN_PASSWORD_LENGTH`
  (default 4). A password equal to the username, and an empty one, are always
  rejected. Setting the minimum to 12 or more additionally enables the
  common-password blocklist and character-variety checks.
  **On a plain-HTTP LAN portal the password is the only barrier to full device and
  HRMS configuration, so a real one is worth using in production.**

### Data handling

- **No biometric data is stored.** No punches, no fingerprint or face templates, no
  employee names. Punches exist in memory during a sync and are discarded. A test
  asserts no ORM model can persist punch-shaped data.
- The bridge is **read-only against terminals**. Buffer clearing, clock setting and
  enrolment are not implemented and are not reachable through any endpoint.
- The HRMS payload contains only what the configured profile names. Fields mapped to
  `null` are omitted entirely — the bridge never adds a field to someone else's API.

### Transport and application

- HTTPS is required for the HRMS base URL unless explicitly overridden for a lab.
- Security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, and a strict CSP. HSTS once TLS is enabled.
- Same-origin portal, so CORS stays disabled.
- Parameterised SQL throughout SQLAlchemy; validated input via Pydantic on every
  endpoint.
- Errors shown to operators carry no stack traces; those go to the log only.

---

## Network exposure

| Direction | Port | Scope |
|---|---|---|
| Inbound | TCP 8080 | Local subnet only |
| Outbound | TCP 4370 | Terminals on the LAN |
| Outbound | TCP 443 | HRMS |

The bridge needs **no inbound internet access** at all.

---

## Operational guidance

- Keep the machine patched and its disk encrypted (BitLocker). Given the API key is
  stored unencrypted, full-disk encryption is a meaningful part of protecting it.
- **Backups contain the API key.** Treat `backups\` with the same care as `data\` and
  do not copy it somewhere less protected.
- Create one portal account per operator rather than sharing one — the log records
  who changed what.
- Review the Logs page after any configuration change.
- Rotate the HRMS API key on the usual schedule; entering a new one in the portal
  replaces it and re-registers the redaction filter immediately.

---

## Reporting a problem

Include the sync run id and the relevant log excerpt. The log is safe to share as-is:
credentials are removed before anything is written.
