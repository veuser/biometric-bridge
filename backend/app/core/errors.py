"""Error taxonomy.

Every failure the sync engine can hit maps to exactly one :class:`SyncStatus`, and
each error knows whether retrying it could plausibly help. That last property is what
keeps the retry policy in one place instead of scattered ``isinstance`` checks
(PLAN.md §18, §19).

Messages on these exceptions are shown to portal users, so they must never carry a
stack trace, a credential, or a raw driver error. The underlying exception goes to the
log via ``__cause__``.
"""

from __future__ import annotations

from app.core.enums import SyncStatus


class BridgeError(Exception):
    """Base class for every error the application raises deliberately."""

    status: SyncStatus = SyncStatus.UNKNOWN_ERROR
    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── Device ────────────────────────────────────────────────────────────────────


class DeviceError(BridgeError):
    status = SyncStatus.DEVICE_UNAVAILABLE


class DeviceUnavailableError(DeviceError):
    """Could not reach or complete a conversation with the device."""

    status = SyncStatus.DEVICE_UNAVAILABLE
    retryable = True


class DeviceTimeoutError(DeviceError):
    status = SyncStatus.TIMEOUT
    retryable = True


class DeviceConfigurationError(DeviceError):
    """Bad IP, bad port, wrong comm key — retrying cannot fix configuration."""

    status = SyncStatus.DEVICE_CONFIGURATION_ERROR
    retryable = False


class DeviceClockRegressionError(DeviceError):
    """Device clock is behind the watermark; the sync window would invert.

    Not retryable: the next attempt reads the same wrong clock. Recovery is an
    operator fixing the device time, then a manual-range sync over the gap.
    """

    status = SyncStatus.DEVICE_CLOCK_REGRESSION
    retryable = False


class DeviceClockJumpError(DeviceError):
    """Device clock moved further than tolerated between two runs."""

    status = SyncStatus.DEVICE_CLOCK_JUMP
    retryable = False


# ── HRMS ──────────────────────────────────────────────────────────────────────


class HrmsError(BridgeError):
    status = SyncStatus.HRMS_UNAVAILABLE


class HrmsUnavailableError(HrmsError):
    """Connection refused, DNS failure, 5xx, or 429."""

    status = SyncStatus.HRMS_UNAVAILABLE
    retryable = True


class HrmsTimeoutError(HrmsError):
    """Timed out waiting for HRMS.

    Treated as a failure and retried, which is only safe if the HRMS attendance
    endpoint is idempotent — the open assumption recorded in PLAN.md §5.5.
    """

    status = SyncStatus.TIMEOUT
    retryable = True


class HrmsAuthenticationError(HrmsError):
    """401/403. Retrying with the same key will fail identically."""

    status = SyncStatus.HRMS_AUTHENTICATION_FAILED
    retryable = False


class HrmsBadRequestError(HrmsError):
    """400/422 — the payload does not match the configured HRMS profile."""

    status = SyncStatus.HRMS_BAD_REQUEST
    retryable = False


class HrmsNotConfiguredError(HrmsError):
    status = SyncStatus.DEVICE_CONFIGURATION_ERROR
    retryable = False


# ── Records ───────────────────────────────────────────────────────────────────


class InvalidRecordError(BridgeError):
    status = SyncStatus.INVALID_RECORD
    retryable = False


# ── Concurrency ───────────────────────────────────────────────────────────────


class SyncInProgressError(BridgeError):
    """Another run holds this device's lock. Surfaces as HTTP 409."""

    status = SyncStatus.RUNNING
    retryable = False
