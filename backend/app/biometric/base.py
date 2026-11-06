"""Device abstraction.

The sync engine talks to this interface, never to pyzk. Adding a ZKTeco or Matrix
connector later means implementing :class:`BiometricDevice`, with no change to the
sync engine, the scheduler or the API.

Everything here is **synchronous**, because the underlying protocol libraries are
blocking socket I/O. :mod:`app.biometric.device_manager` is responsible for keeping
those calls off the event loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class RawPunch:
    """One attendance record exactly as the device reported it.

    Deliberately un-interpreted: ``status`` and ``punch`` are kept as the integers
    the device sent so that a firmware whose encoding differs from the documented
    one can be diagnosed from the portal instead of guessed at (PLAN.md §1.1).

    ``timestamp`` is **naive device-local time**. Converting it is the caller's job.
    """

    employee_code: str
    #: Enrolment name held on the terminal. Not part of the attendance record —
    #: the device reports it on a separate user table — so it is carried purely so
    #: a punch that cannot be attributed can name the person rather than only the
    #: code. Empty when the terminal has no name for that user.
    employee_name: str | None
    timestamp: datetime
    status: int
    punch: int


@dataclass(slots=True)
class DeviceUser:
    user_id: str
    name: str


@dataclass(slots=True)
class DeviceInfo:
    serial_number: str | None = None
    firmware_version: str | None = None
    platform: str | None = None
    device_name: str | None = None
    user_count: int | None = None
    record_count: int | None = None
    device_time: datetime | None = None


@dataclass(slots=True)
class AttendanceReadResult:
    """Outcome of one attendance read.

    ``buffer_records`` is the size of the device's whole attendance buffer, which the
    ZK protocol forces us to transfer in full because it has no server-side time
    filter (PLAN.md §1.2). It is the dominant cost of the system, so it is reported
    separately from the in-window count rather than being conflated with it.
    """

    punches: list[RawPunch] = field(default_factory=list)
    buffer_records: int = 0
    read_duration_ms: int = 0

    @property
    def window_records(self) -> int:
        return len(self.punches)


@dataclass(slots=True)
class ConnectionTestResult:
    reachable: bool
    message: str
    info: DeviceInfo | None = None
    #: device clock minus bridge clock. Positive means the device runs fast.
    clock_offset_seconds: float | None = None


class BiometricDevice(ABC):
    """A biometric attendance terminal the bridge can read from.

    Read-only by design. No implementation may expose clearing the device buffer or
    setting the device clock: the bridge is not the system of record and must never be
    able to destroy attendance data on the hardware. The single permitted write,
    adding a user, lives on the separate :class:`EnrollableDevice` interface so the
    attendance sync path cannot reach it.
    """

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Probe the device. Must not raise for an unreachable device — an operator
        pressing "Test Connection" wants a diagnosis, not a stack trace."""

    @abstractmethod
    def get_device_info(self) -> DeviceInfo: ...

    @abstractmethod
    def get_device_time(self) -> datetime:
        """The device's own clock, naive. The sync window is built from this rather
        than the bridge clock, so drift cannot silently truncate the window."""

    @abstractmethod
    def get_attendance_logs(
        self, from_time: datetime, to_time: datetime
    ) -> AttendanceReadResult:
        """Records with ``from_time <= timestamp <= to_time``, in device-local time."""

    @abstractmethod
    def get_users(self) -> list[DeviceUser]: ...


@dataclass(slots=True)
class DeviceUserRecord:
    """A user as stored on the terminal, with the fields needed to add one safely.

    ``uid`` is the terminal's internal slot number. It is distinct from ``user_id``
    (the employee code) and writing to an occupied ``uid`` silently overwrites that
    user, so callers must pick an unused one.
    """

    uid: int
    user_id: str
    name: str
    #: Card number as digits, or ``None`` when the user has no card.
    card: str | None = None


class EnrollableDevice(ABC):
    """The one and only write capability the bridge has against a terminal: **adding a user**.

    Deliberately a separate interface from :class:`BiometricDevice`. The read path
    (attendance sync) takes a ``BiometricDevice`` and cannot reach anything here, so a
    bug in the sync engine cannot modify a terminal. And the surface is deliberately
    tiny: there is no update, no delete, no clearing of data and no clock setting.
    ``tests/unit/test_enrollment_write_surface.py`` fails if that ever grows.

    Fingerprints cannot be captured remotely, so a user added here has a name and
    optionally a card; a finger, if wanted, is enrolled on the terminal itself.
    """

    @abstractmethod
    def list_user_records(self) -> list[DeviceUserRecord]: ...

    @abstractmethod
    def add_user(self, *, uid: int, user_id: str, name: str, card: str | None) -> None:
        """Create a normal (non-admin) user in the unused slot ``uid``.

        Callers must have checked that ``uid`` and ``user_id`` are both unused; this
        method does not protect an existing user from being overwritten.
        """
