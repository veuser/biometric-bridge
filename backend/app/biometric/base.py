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

    Read-only by design. No implementation may expose clearing the device buffer,
    setting the device clock, or enrolling users: the bridge is not the system of
    record and must never be able to destroy attendance data on the hardware.
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
