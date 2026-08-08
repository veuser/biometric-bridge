"""Clock conversion.

The device hands over naive local timestamps and the rest of the system speaks
tz-aware UTC. Getting this boundary wrong is how attendance records land on the wrong
day, so it is tested directly rather than only through the sync engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.utils.datetime_utils import (
    device_naive_to_utc,
    ensure_aware,
    isoformat_utc,
    utc_to_device_naive,
    utcnow,
)

IST = ZoneInfo("Asia/Kolkata")


def test_device_naive_to_utc_applies_the_offset() -> None:
    # 12:07:09 IST is 06:37:09 UTC — IST is UTC+5:30.
    result = device_naive_to_utc(datetime(2026, 8, 8, 12, 7, 9), IST)
    assert result == datetime(2026, 8, 8, 6, 37, 9, tzinfo=UTC)
    assert result.tzinfo is not None


def test_round_trip_is_lossless_for_a_zone_without_dst() -> None:
    naive = datetime(2026, 8, 8, 12, 7, 9)
    assert utc_to_device_naive(device_naive_to_utc(naive, IST), IST) == naive


def test_device_naive_to_utc_rejects_an_aware_input() -> None:
    """Passing an already-converted value would double-apply the offset."""
    with pytest.raises(ValueError, match="naive"):
        device_naive_to_utc(datetime(2026, 8, 8, 12, 0, tzinfo=UTC), IST)


def test_utc_to_device_naive_rejects_a_naive_input() -> None:
    with pytest.raises(ValueError, match="aware"):
        utc_to_device_naive(datetime(2026, 8, 8, 12, 0), IST)


def test_ambiguous_dst_time_resolves_deterministically() -> None:
    """Asia/Kolkata has no DST, but the timezone is configurable.

    01:30 on 2026-11-01 occurs twice in America/New_York. fold=0 picks the first
    (EDT, UTC-4) every time, so repeated syncs cannot disagree with each other.
    """
    ny = ZoneInfo("America/New_York")
    result = device_naive_to_utc(datetime(2026, 11, 1, 1, 30), ny)
    assert result == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_ensure_aware_normalises_to_utc() -> None:
    value = datetime(2026, 8, 8, 12, 0, tzinfo=IST)
    assert ensure_aware(value) == datetime(2026, 8, 8, 6, 30, tzinfo=UTC)


def test_ensure_aware_rejects_naive_with_the_field_name() -> None:
    with pytest.raises(ValueError, match="from_time"):
        ensure_aware(datetime(2026, 8, 8, 12, 0), field="from_time")


def test_utcnow_is_aware() -> None:
    assert utcnow().tzinfo is UTC


def test_isoformat_utc_handles_none() -> None:
    assert isoformat_utc(None) is None
    stamp = isoformat_utc(datetime(2026, 8, 8, 6, 37, 9, tzinfo=UTC))
    assert stamp is not None and stamp.startswith("2026-08-08T06:37:09")
