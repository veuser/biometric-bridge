"""Domain enumerations.

These are stored as strings in SQLite and returned verbatim by the API, so the
*values* are a compatibility surface: renaming one is a migration, not a refactor.
"""

from __future__ import annotations

from enum import StrEnum


class DeviceType(StrEnum):
    ESSL = "ESSL"


class DeviceRole(StrEnum):
    """What a device's punches mean.

    The reference deployment has one entry device and one exit device, so the punch
    type comes from *which* device reported it. ``FROM_DEVICE`` defers to the record's
    own punch-state field for mixed-mode devices (PLAN.md §2).
    """

    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    FROM_DEVICE = "FROM_DEVICE"


class PunchType(StrEnum):
    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    BREAK_OUT = "BREAK_OUT"
    BREAK_IN = "BREAK_IN"
    OT_IN = "OT_IN"
    OT_OUT = "OT_OUT"
    UNKNOWN = "UNKNOWN"


class VerificationType(StrEnum):
    PASSWORD = "PASSWORD"
    FINGERPRINT = "FINGERPRINT"
    CARD = "CARD"
    FACE = "FACE"
    UNKNOWN = "UNKNOWN"


class SyncTrigger(StrEnum):
    """How a sync run started — determines watermark semantics (PLAN.md §5.7).

    ``MANUAL_RANGE`` is the only trigger that never advances the watermark.
    """

    SCHEDULED = "SCHEDULED"
    CATCH_UP = "CATCH_UP"
    MANUAL_RANGE = "MANUAL_RANGE"
    STARTUP = "STARTUP"

    @property
    def advances_watermark(self) -> bool:
        return self is not SyncTrigger.MANUAL_RANGE


class SyncStatus(StrEnum):
    """Terminal (and one non-terminal) state of a sync run."""

    RUNNING = "RUNNING"

    SUCCESS = "SUCCESS"
    NO_RECORDS = "NO_RECORDS"
    PARTIAL = "PARTIAL"

    FAILED = "FAILED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    DEVICE_CONFIGURATION_ERROR = "DEVICE_CONFIGURATION_ERROR"
    DEVICE_CLOCK_REGRESSION = "DEVICE_CLOCK_REGRESSION"
    DEVICE_CLOCK_JUMP = "DEVICE_CLOCK_JUMP"
    HRMS_UNAVAILABLE = "HRMS_UNAVAILABLE"
    HRMS_AUTHENTICATION_FAILED = "HRMS_AUTHENTICATION_FAILED"
    HRMS_BAD_REQUEST = "HRMS_BAD_REQUEST"
    #: Nowhere to send: no base URL, no API key, or sync not enabled. Distinct from the
    #: DEVICE_* statuses because the fix is on the HRMS page and the terminal is fine —
    #: reporting it as a device error sends an operator to debug hardware that works.
    HRMS_NOT_CONFIGURED = "HRMS_NOT_CONFIGURED"
    INVALID_RECORD = "INVALID_RECORD"
    TIMEOUT = "TIMEOUT"
    INTERRUPTED = "INTERRUPTED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

    @property
    def is_terminal(self) -> bool:
        return self is not SyncStatus.RUNNING

    @property
    def is_success(self) -> bool:
        return self in {SyncStatus.SUCCESS, SyncStatus.NO_RECORDS, SyncStatus.PARTIAL}


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    VIEWER = "VIEWER"
