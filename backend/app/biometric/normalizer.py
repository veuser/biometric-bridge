"""Turning raw device records into the normalized punch the HRMS client sends.

The normalized punch is **never persisted** (RULE 1). It exists for the duration of a
sync run and is then discarded, which is why this is a plain frozen dataclass rather
than an ORM model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.biometric.base import RawPunch
from app.biometric.mapping import decode_punch_type, decode_verification
from app.core.enums import DeviceRole, PunchType, VerificationType
from app.core.errors import InvalidRecordError
from app.core.logging import get_logger
from app.utils.datetime_utils import device_naive_to_utc

logger = get_logger(__name__)

CLIENT_RECORD_ID_LENGTH = 32


@dataclass(frozen=True, slots=True)
class BiometricPunch:
    employee_code: str
    #: Only ever used for diagnostics and for the optional name field on the wire.
    #: HRMS holds the authoritative name against the employee record.
    employee_name: str | None
    punch_time: datetime  # timezone-aware UTC
    punch_type: PunchType
    verification_type: VerificationType
    device_id: int
    device_serial_number: str | None
    raw_status: int
    raw_punch: int
    client_record_id: str

    @property
    def label(self) -> str:
        """How this person is named in anything an operator reads.

        The code alone is what HRMS matches on, but it is not what a human
        recognises. "IQ-1004 (Manoj Settebathula)" turns a rejection list into an
        enrolment list somebody can work through.
        """
        return f"{self.employee_code} ({self.employee_name})" if self.employee_name else self.employee_code


def make_client_record_id(
    *, device_serial: str | None, employee_code: str, punch_time: datetime, raw_punch: int
) -> str:
    """A stable identity for one punch.

    Deterministic across retries and across process restarts, which is what makes it
    usable both for in-run deduplication and for the already-pushed suppression
    cache. It is sent to HRMS only if the configured profile has a field for it —
    the bridge never invents a field in someone else's API contract.
    """
    material = "|".join(
        [
            device_serial or "",
            employee_code,
            punch_time.astimezone(tz=None).isoformat() if punch_time.tzinfo else str(punch_time),
            str(raw_punch),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:CLIENT_RECORD_ID_LENGTH]


def normalize(
    raw: RawPunch,
    *,
    device_id: int,
    device_role: DeviceRole,
    device_serial: str | None,
    tz: ZoneInfo,
) -> BiometricPunch:
    """Convert one raw record. Raises :class:`InvalidRecordError` if unusable."""
    employee_code = raw.employee_code.strip()
    if not employee_code:
        raise InvalidRecordError("Device record has no employee code.")

    if raw.timestamp.tzinfo is not None:
        raise InvalidRecordError("Device record timestamp was already timezone-aware.")

    punch_time = device_naive_to_utc(raw.timestamp, tz)

    return BiometricPunch(
        employee_code=employee_code,
        employee_name=(raw.employee_name or None),
        punch_time=punch_time,
        punch_type=decode_punch_type(raw.punch, device_role),
        verification_type=decode_verification(raw.status),
        device_id=device_id,
        device_serial_number=device_serial,
        raw_status=raw.status,
        raw_punch=raw.punch,
        client_record_id=make_client_record_id(
            device_serial=device_serial,
            employee_code=employee_code,
            punch_time=punch_time,
            raw_punch=raw.punch,
        ),
    )


@dataclass(slots=True)
class NormalizationResult:
    punches: list[BiometricPunch]
    invalid_count: int


def normalize_many(
    records: list[RawPunch],
    *,
    device_id: int,
    device_role: DeviceRole,
    device_serial: str | None,
    tz: ZoneInfo,
) -> NormalizationResult:
    """Normalize a batch, dropping and counting unusable records.

    One bad record must never cost the whole window: the rest of the batch is still
    worth pushing, and the count surfaces on the sync run so a persistently
    misbehaving terminal is visible.

    Duplicates within the batch are collapsed by ``client_record_id`` — some
    firmware reports the same punch twice.
    """
    punches: dict[str, BiometricPunch] = {}
    invalid = 0

    for record in records:
        try:
            punch = normalize(
                record,
                device_id=device_id,
                device_role=device_role,
                device_serial=device_serial,
                tz=tz,
            )
        except InvalidRecordError as exc:
            invalid += 1
            logger.warning(
                "Discarding unusable device record.",
                extra={"device_id": device_id, "reason": exc.message},
            )
            continue
        punches[punch.client_record_id] = punch

    ordered = sorted(punches.values(), key=lambda p: p.punch_time)
    return NormalizationResult(punches=ordered, invalid_count=invalid)
