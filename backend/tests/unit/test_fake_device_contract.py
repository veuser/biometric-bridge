"""The fake device must honour the same contract as the real connector.

If it drifts, every sync test built on it is testing a fiction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.biometric.base import BiometricDevice
from app.core.errors import DeviceUnavailableError
from tests.fakes.fake_device import FakeDevice, unreachable_device


def test_fake_implements_the_interface() -> None:
    assert isinstance(FakeDevice(), BiometricDevice)


def test_window_filter_is_inclusive_at_both_ends() -> None:
    """The overlap window depends on boundary records being returned."""
    device = FakeDevice()
    device.add_punch("IQ-1", datetime(2026, 8, 8, 12, 0))
    device.add_punch("IQ-2", datetime(2026, 8, 8, 12, 5))
    device.add_punch("IQ-3", datetime(2026, 8, 8, 12, 10))

    result = device.get_attendance_logs(
        datetime(2026, 8, 8, 12, 0), datetime(2026, 8, 8, 12, 10)
    )
    assert [p.employee_code for p in result.punches] == ["IQ-1", "IQ-2", "IQ-3"]


def test_records_outside_the_window_are_excluded() -> None:
    device = FakeDevice()
    device.add_punch("old", datetime(2026, 8, 8, 11, 0))
    device.add_punch("new", datetime(2026, 8, 8, 13, 0))
    result = device.get_attendance_logs(
        datetime(2026, 8, 8, 12, 0), datetime(2026, 8, 8, 12, 30)
    )
    assert result.punches == []


def test_buffer_count_reports_the_whole_buffer_not_the_window() -> None:
    """buffer_records is the cost driver; conflating it with the window hides it."""
    device = FakeDevice()
    for minute in range(10):
        device.add_punch(f"IQ-{minute}", datetime(2026, 8, 8, 11, minute))
    result = device.get_attendance_logs(
        datetime(2026, 8, 8, 11, 0), datetime(2026, 8, 8, 11, 2)
    )
    assert result.buffer_records == 10
    assert result.window_records == 3


def test_results_are_sorted_by_time() -> None:
    device = FakeDevice()
    device.add_punch("late", datetime(2026, 8, 8, 12, 9))
    device.add_punch("early", datetime(2026, 8, 8, 12, 1))
    result = device.get_attendance_logs(
        datetime(2026, 8, 8, 12, 0), datetime(2026, 8, 8, 12, 30)
    )
    assert [p.employee_code for p in result.punches] == ["early", "late"]


def test_transient_failure_then_recovery() -> None:
    """Models a terminal that is briefly unreachable — the retry path needs it."""
    device = FakeDevice(fail_with=DeviceUnavailableError("down"), fail_times=1)
    with pytest.raises(DeviceUnavailableError):
        device.get_device_time()
    assert isinstance(device.get_device_time(), datetime)


def test_unreachable_device_test_connection_reports_not_raises() -> None:
    assert unreachable_device().test_connection().reachable is False


def test_clock_can_be_advanced_and_rewound() -> None:
    """Both directions matter: a corrected fast clock jumps backwards."""
    device = FakeDevice(device_time=datetime(2026, 8, 8, 12, 0))
    device.advance_clock(timedelta(minutes=5))
    assert device.device_time == datetime(2026, 8, 8, 12, 5)
    device.advance_clock(timedelta(minutes=-30))
    assert device.device_time == datetime(2026, 8, 8, 11, 35)
