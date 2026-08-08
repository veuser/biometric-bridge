"""A scriptable BiometricDevice.

Lets every sync scenario — clock drift, empty buffers, malformed records, flaky
connections — be tested deterministically without hardware. The real terminals are
on a customer LAN and cannot be made to misbehave on demand.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.biometric.base import (
    AttendanceReadResult,
    BiometricDevice,
    ConnectionTestResult,
    DeviceInfo,
    DeviceUser,
    RawPunch,
)
from app.core.errors import DeviceUnavailableError


class FakeDevice(BiometricDevice):
    def __init__(
        self,
        *,
        buffer: list[RawPunch] | None = None,
        device_time: datetime | None = None,
        serial_number: str = "FAKE-0001",
        users: list[DeviceUser] | None = None,
        fail_with: Exception | None = None,
        fail_times: int = 0,
    ) -> None:
        self.buffer = buffer or []
        self.device_time = device_time or datetime(2026, 8, 8, 12, 20, 0)
        self.serial_number = serial_number
        self.users = users or []
        #: Raise this on the next ``fail_times`` reads, then behave. Models a
        #: terminal that is briefly unreachable and then recovers.
        self.fail_with = fail_with
        self.fail_times = fail_times

        self.connect_count = 0
        self.disconnect_count = 0
        self.read_count = 0

    # ── scripting helpers ────────────────────────────────────────────────────

    def add_punch(
        self, employee_code: str, timestamp: datetime, *, status: int = 1, punch: int = 0
    ) -> None:
        self.buffer.append(
            RawPunch(employee_code=employee_code, timestamp=timestamp, status=status, punch=punch)
        )

    def advance_clock(self, delta: timedelta) -> None:
        self.device_time += delta

    def _maybe_fail(self) -> None:
        if self.fail_times > 0 and self.fail_with is not None:
            self.fail_times -= 1
            raise self.fail_with

    # ── BiometricDevice ──────────────────────────────────────────────────────

    def connect(self) -> None:
        self.connect_count += 1
        self._maybe_fail()

    def disconnect(self) -> None:
        self.disconnect_count += 1

    def test_connection(self) -> ConnectionTestResult:
        try:
            self._maybe_fail()
        except Exception as exc:
            return ConnectionTestResult(reachable=False, message=str(exc))
        return ConnectionTestResult(
            reachable=True,
            message="Connected.",
            info=self.get_device_info(),
            clock_offset_seconds=0.0,
        )

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            serial_number=self.serial_number,
            firmware_version="Ver 6.60",
            platform="ZMM220",
            device_name="FakeTerminal",
            user_count=len(self.users),
            record_count=len(self.buffer),
            device_time=self.device_time,
        )

    def get_device_time(self) -> datetime:
        self._maybe_fail()
        return self.device_time

    def get_attendance_logs(
        self, from_time: datetime, to_time: datetime
    ) -> AttendanceReadResult:
        self.read_count += 1
        self._maybe_fail()
        in_window = sorted(
            (p for p in self.buffer if from_time <= p.timestamp <= to_time),
            key=lambda p: p.timestamp,
        )
        return AttendanceReadResult(
            punches=list(in_window), buffer_records=len(self.buffer), read_duration_ms=1
        )

    def get_users(self) -> list[DeviceUser]:
        self._maybe_fail()
        return list(self.users)


def unreachable_device() -> FakeDevice:
    return FakeDevice(fail_with=DeviceUnavailableError("Terminal is unreachable."), fail_times=99)
