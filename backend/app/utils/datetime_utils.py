"""Timezone conversion helpers.

The bridge deals with two clocks that must never be confused:

* **Device clock** — naive ``datetime`` in the device's own local time, which may
  drift from real time and which the bridge never corrects.
* **Bridge clock** — the Windows host's clock, used for run bookkeeping.

Everything crossing a service boundary is timezone-aware UTC. These helpers are the
only sanctioned way across that boundary (PLAN.md §5.3, spec RULE 15).
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def device_naive_to_utc(naive: datetime, tz: ZoneInfo) -> datetime:
    """Interpret a device-local naive timestamp in ``tz`` and convert to UTC.

    ``fold=0`` resolves the ambiguous hour of a DST fall-back deterministically. The
    reference deployment is Asia/Kolkata, which has no DST, but the timezone is
    configurable so the code must not assume that.
    """
    if naive.tzinfo is not None:
        raise ValueError("expected a naive device timestamp, got an aware one")
    return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)


def utc_to_device_naive(aware: datetime, tz: ZoneInfo) -> datetime:
    """Inverse of :func:`device_naive_to_utc`.

    Needed because the watermark is stored in UTC but the sync window has to be
    expressed in device-clock terms to filter records the device gave us.
    """
    if aware.tzinfo is None:
        raise ValueError("expected an aware datetime, got a naive one")
    return aware.astimezone(tz).replace(tzinfo=None)


def ensure_aware(value: datetime, *, field: str = "datetime") -> datetime:
    """Guard for values arriving from outside (API payloads, config)."""
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def isoformat_utc(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()
