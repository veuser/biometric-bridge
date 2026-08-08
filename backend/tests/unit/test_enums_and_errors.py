"""Watermark semantics and retry classification live in the type system.

Encoding these as properties on the enums and exceptions keeps the two rules that
cause data loss — "which triggers advance the watermark" and "which failures are
worth retrying" — in one place rather than scattered across the sync engine.
"""

from __future__ import annotations

import pytest

from app.core.enums import SyncStatus, SyncTrigger
from app.core.errors import (
    BridgeError,
    DeviceClockJumpError,
    DeviceClockRegressionError,
    DeviceConfigurationError,
    DeviceTimeoutError,
    DeviceUnavailableError,
    HrmsAuthenticationError,
    HrmsBadRequestError,
    HrmsTimeoutError,
    HrmsUnavailableError,
    InvalidRecordError,
    SyncInProgressError,
)


def test_only_manual_range_leaves_the_watermark_alone() -> None:
    """RULE 5: an operator's historical sync must not disturb automatic sync."""
    assert SyncTrigger.MANUAL_RANGE.advances_watermark is False
    for trigger in (SyncTrigger.SCHEDULED, SyncTrigger.CATCH_UP, SyncTrigger.STARTUP):
        assert trigger.advances_watermark is True


def test_running_is_the_only_non_terminal_status() -> None:
    assert SyncStatus.RUNNING.is_terminal is False
    for status in SyncStatus:
        if status is not SyncStatus.RUNNING:
            assert status.is_terminal is True


def test_success_statuses() -> None:
    assert SyncStatus.SUCCESS.is_success
    assert SyncStatus.NO_RECORDS.is_success
    assert SyncStatus.PARTIAL.is_success
    assert not SyncStatus.FAILED.is_success
    assert not SyncStatus.DEVICE_CLOCK_REGRESSION.is_success


@pytest.mark.parametrize(
    "error_class",
    [DeviceUnavailableError, DeviceTimeoutError, HrmsUnavailableError, HrmsTimeoutError],
)
def test_transient_failures_are_retryable(error_class: type[BridgeError]) -> None:
    assert error_class("boom").retryable is True


@pytest.mark.parametrize(
    "error_class",
    [
        HrmsAuthenticationError,
        HrmsBadRequestError,
        DeviceConfigurationError,
        InvalidRecordError,
        DeviceClockRegressionError,
        DeviceClockJumpError,
    ],
)
def test_deterministic_failures_are_not_retryable(error_class: type[BridgeError]) -> None:
    """Retrying a bad key, a bad payload or a bad clock just wastes the window."""
    assert error_class("boom").retryable is False


def test_each_error_maps_to_a_distinct_meaningful_status() -> None:
    assert DeviceClockRegressionError("x").status is SyncStatus.DEVICE_CLOCK_REGRESSION
    assert DeviceClockJumpError("x").status is SyncStatus.DEVICE_CLOCK_JUMP
    assert HrmsAuthenticationError("x").status is SyncStatus.HRMS_AUTHENTICATION_FAILED
    assert HrmsBadRequestError("x").status is SyncStatus.HRMS_BAD_REQUEST
    assert HrmsTimeoutError("x").status is SyncStatus.TIMEOUT


def test_message_is_preserved_for_the_portal() -> None:
    assert SyncInProgressError("Synchronization is already in progress.").message == (
        "Synchronization is already in progress."
    )
