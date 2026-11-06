"""eSSL / ZK terminal connector, built on pyzk.

Two protocol realities shape this module and are worth stating plainly:

1. **There is no server-side time filter.** ``get_attendance()`` transfers the entire
   on-device buffer and the caller filters in Python. A busy terminal can hold tens
   of thousands of records, so every poll pays that cost. The read is timed and the
   buffer size reported so the expense is visible on the dashboard rather than being
   a mystery slowdown.

2. **Timestamps are naive device-local time.** Nothing here converts them; that
   happens in the normalizer, which knows the configured timezone. Keeping the
   conversion out of this module is what allows the clock-drift guard to compare
   like with like.

This module is the only place in the application allowed to hold naive datetimes,
which is enforced by the ruff DTZ per-file ignore in ``pyproject.toml``.
"""

from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any

from zk import ZK
from zk.exception import ZKErrorConnection, ZKErrorResponse, ZKNetworkError

from app.biometric.base import (
    AttendanceReadResult,
    BiometricDevice,
    ConnectionTestResult,
    DeviceInfo,
    DeviceUser,
    DeviceUserRecord,
    EnrollableDevice,
    RawPunch,
)
from app.core.errors import (
    DeviceConfigurationError,
    DeviceTimeoutError,
    DeviceUnavailableError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


#: Longest user id and name the terminal's user table can hold. Longer values are cut by
#: the firmware without error, so the bridge cuts them itself and says so.
MAX_USER_ID_LENGTH = 24
MAX_NAME_LENGTH = 24


class EsslBiometricDevice(BiometricDevice, EnrollableDevice):
    def __init__(
        self,
        *,
        ip_address: str,
        port: int = 4370,
        timeout_seconds: int = 10,
        comm_key: str | None = None,
        force_udp: bool = False,
        label: str = "device",
    ) -> None:
        self._ip = ip_address
        self._port = port
        self._timeout = timeout_seconds
        self._force_udp = force_udp
        self._label = label
        self._comm_key = _parse_comm_key(comm_key)
        self._conn: Any = None

    # ── connection ───────────────────────────────────────────────────────────

    def connect(self) -> None:
        zk = ZK(
            self._ip,
            port=self._port,
            timeout=self._timeout,
            password=self._comm_key,
            force_udp=self._force_udp,
            # pyzk otherwise shells out to the OS `ping` before connecting. That
            # spawns a subprocess from inside a Windows service, adds seconds of
            # latency, and fails outright on the many networks that drop ICMP —
            # while the TCP connect below already proves reachability.
            ommit_ping=True,
        )
        try:
            self._conn = zk.connect()
        except Exception as exc:
            raise _translate(exc, self._label, self._ip, self._port) from exc

    def disconnect(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.disconnect()
        except Exception:
            # Teardown failures are not actionable and must never mask the real
            # error from whatever the caller was actually doing.
            logger.debug("Ignoring error while disconnecting.", exc_info=True)
        finally:
            self._conn = None

    def __enter__(self) -> EsslBiometricDevice:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.disconnect()

    # ── reads ────────────────────────────────────────────────────────────────

    def test_connection(self) -> ConnectionTestResult:
        """Never raises — an operator pressing "Test Connection" wants a diagnosis."""
        started = time.monotonic()
        try:
            with self:
                info = self._read_info()
        except DeviceConfigurationError as exc:
            return ConnectionTestResult(reachable=False, message=exc.message)
        except (DeviceUnavailableError, DeviceTimeoutError) as exc:
            return ConnectionTestResult(reachable=False, message=exc.message)
        except Exception:
            logger.exception("Unexpected error testing device.", extra={"device": self._label})
            return ConnectionTestResult(
                reachable=False, message="Unexpected error contacting the device."
            )

        offset = None
        if info.device_time is not None:
            offset = (info.device_time - datetime.now()).total_seconds()

        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ConnectionTestResult(
            reachable=True,
            message=f"Connected in {elapsed_ms} ms.",
            info=info,
            clock_offset_seconds=offset,
        )

    def get_device_info(self) -> DeviceInfo:
        return self._with_connection(lambda: self._read_info())

    def get_device_time(self) -> datetime:
        def read() -> datetime:
            value = self._conn.get_time()
            if not isinstance(value, datetime):
                raise DeviceUnavailableError(
                    f"{self._label} returned an unreadable device clock."
                )
            return value

        return self._with_connection(read)

    def get_users(self) -> list[DeviceUser]:
        def read() -> list[DeviceUser]:
            return [
                DeviceUser(user_id=str(u.user_id), name=str(u.name or ""))
                for u in self._conn.get_users()
            ]

        return self._with_connection(read)

    # ── the one write: add a user ────────────────────────────────────────────

    def list_user_records(self) -> list[DeviceUserRecord]:
        def read() -> list[DeviceUserRecord]:
            records: list[DeviceUserRecord] = []
            for u in self._conn.get_users() or []:
                card = getattr(u, "card", 0)
                records.append(
                    DeviceUserRecord(
                        uid=int(u.uid),
                        user_id=str(u.user_id).strip(),
                        name=str(u.name or "").strip(),
                        card=str(int(card)) if card else None,
                    )
                )
            return records

        return self._with_connection(read)

    def add_user(self, *, uid: int, user_id: str, name: str, card: str | None) -> None:
        if len(user_id) > MAX_USER_ID_LENGTH:
            # Truncating a user id would register the employee under a code HRMS does
            # not know, so their punches would come back as unknown. Refuse instead.
            raise DeviceConfigurationError(
                f"Employee code {user_id!r} is longer than the {MAX_USER_ID_LENGTH} "
                "characters the terminal can store."
            )

        def write() -> None:
            self._conn.set_user(
                uid=uid,
                name=name[:MAX_NAME_LENGTH],
                privilege=0,  # USER_DEFAULT: never an administrator
                password="",
                group_id="",
                user_id=user_id,
                card=int(card) if card else 0,
            )

        self._with_connection(write)

    def get_attendance_logs(
        self, from_time: datetime, to_time: datetime
    ) -> AttendanceReadResult:
        if from_time > to_time:
            # Guards against an inverted window reaching the device. The sync engine
            # catches clock regression earlier, but this class is public API.
            raise DeviceConfigurationError(
                "The start of the window is after its end; refusing to read."
            )

        def read() -> AttendanceReadResult:
            started = time.monotonic()
            raw = self._conn.get_attendance() or []
            elapsed_ms = int((time.monotonic() - started) * 1000)

            # Attendance records carry only the user id; names live on a separate
            # table. This is a second round trip, but a cheap one next to the buffer
            # read (110 users against ~20k records), and pyzk already fetches the
            # same table internally during get_attendance().
            #
            # Never fails the read: a punch with no name is still a punch, and
            # losing a whole cycle because the name lookup hiccuped would trade
            # attendance data for a diagnostic nicety.
            names: dict[str, str] = {}
            try:
                for user in self._conn.get_users() or []:
                    name = str(getattr(user, "name", "") or "").strip()
                    if name:
                        names[str(user.user_id).strip()] = name
            except Exception:
                logger.warning(
                    "Could not read the device user table; punches will have no names.",
                    extra={"device": self._label},
                    exc_info=True,
                )

            punches: list[RawPunch] = []
            for record in raw:
                timestamp = getattr(record, "timestamp", None)
                if not isinstance(timestamp, datetime):
                    # A single malformed record must not lose the whole batch.
                    logger.warning(
                        "Skipping device record with an unreadable timestamp.",
                        extra={"device": self._label},
                    )
                    continue
                if not (from_time <= timestamp <= to_time):
                    continue
                punches.append(
                    RawPunch(
                        employee_code=str(record.user_id).strip(),
                        employee_name=names.get(str(record.user_id).strip()),
                        timestamp=timestamp,
                        status=int(getattr(record, "status", -1)),
                        punch=int(getattr(record, "punch", -1)),
                    )
                )

            punches.sort(key=lambda p: p.timestamp)
            logger.info(
                "Read attendance buffer.",
                extra={
                    "device": self._label,
                    "buffer_records": len(raw),
                    "window_records": len(punches),
                    "read_duration_ms": elapsed_ms,
                },
            )
            return AttendanceReadResult(
                punches=punches, buffer_records=len(raw), read_duration_ms=elapsed_ms
            )

        return self._with_connection(read)

    # ── internals ────────────────────────────────────────────────────────────

    def _read_info(self) -> DeviceInfo:
        """Best-effort: an unsupported field yields None rather than failing the call.

        Firmware variants differ in which of these they answer, and none of them is
        important enough to fail a connection test over.
        """

        def safe(getter: str) -> Any:
            try:
                return getattr(self._conn, getter)()
            except Exception:
                logger.debug("Device did not answer %s.", getter, exc_info=True)
                return None

        info = DeviceInfo(
            serial_number=_as_str(safe("get_serialnumber")),
            firmware_version=_as_str(safe("get_firmware_version")),
            platform=_as_str(safe("get_platform")),
            device_name=_as_str(safe("get_device_name")),
            device_time=safe("get_time"),
        )
        try:
            self._conn.read_sizes()
            info.user_count = int(self._conn.users)
            info.record_count = int(self._conn.records)
        except Exception:
            logger.debug("Device did not report buffer sizes.", exc_info=True)
        return info

    def _with_connection(self, operation: Any) -> Any:
        """Run ``operation`` inside a connection, translating protocol errors."""
        already_open = self._conn is not None
        if not already_open:
            self.connect()
        try:
            return operation()
        except Exception as exc:
            raise _translate(exc, self._label, self._ip, self._port) from exc
        finally:
            if not already_open:
                self.disconnect()


def _parse_comm_key(comm_key: str | None) -> int:
    """ZK comm keys are numeric. A non-numeric value is a configuration mistake."""
    if not comm_key:
        return 0
    try:
        return int(comm_key)
    except ValueError as exc:
        raise DeviceConfigurationError(
            "The device communication key must be a number."
        ) from exc


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _translate(exc: Exception, label: str, ip: str, port: int) -> Exception:
    """Map a protocol/socket failure onto the bridge's error taxonomy.

    The distinction that matters is retryable vs not: a timeout is worth another
    attempt, a refused connection to a misconfigured address is not.
    """
    if isinstance(exc, DeviceConfigurationError | DeviceUnavailableError | DeviceTimeoutError):
        return exc

    if isinstance(exc, socket.timeout | TimeoutError):
        return DeviceTimeoutError(f"{label} did not respond within the timeout.")

    if isinstance(exc, ZKErrorResponse):
        # The device answered and refused. pyzk raises this for authentication failure
        # AND for any command the firmware rejects, so the two are separated here:
        # blaming the comm key for an unrelated refusal sends an operator to change a
        # setting that was already correct.
        detail = str(exc).strip()
        if "unauth" in detail.lower():
            return DeviceConfigurationError(
                f"{label} rejected the communication key. If the terminal has no comm "
                "key set, leave the field blank — a value here must match the device "
                "exactly, and a wrong one fails the same way as a missing one."
            )
        # Carry the device's own words. They are the only clue to a refusal that is not
        # about authentication, and they were previously discarded.
        return DeviceConfigurationError(
            f"{label} rejected the request ({detail or 'no reason given'})."
        )

    if isinstance(exc, ZKNetworkError | ZKErrorConnection | ConnectionError | OSError):
        return DeviceUnavailableError(f"{label} is unreachable at {ip}:{port}.")

    return DeviceUnavailableError(f"{label} could not be read.")
