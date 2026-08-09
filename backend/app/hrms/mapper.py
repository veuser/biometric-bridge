"""Turning normalized punches into whatever body the configured HRMS expects."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.biometric.normalizer import BiometricPunch
from app.hrms.profile import BatchMode, DateTimeFormat, HrmsProfile


def format_timestamp(value: datetime, profile: HrmsProfile) -> str:
    """Render a UTC punch time in the format and timezone the HRMS wants."""
    tz = ZoneInfo(profile.output_timezone)
    if profile.datetime_format is DateTimeFormat.ISO8601_UTC_Z:
        # Python renders UTC as "+00:00"; APIs asking for Z generally mean Z.
        return value.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
    if profile.datetime_format is DateTimeFormat.ISO8601_OFFSET:
        return value.astimezone(tz).isoformat()
    return value.astimezone(tz).strftime(profile.datetime_pattern)


def punch_to_record(punch: BiometricPunch, profile: HrmsProfile) -> dict[str, Any]:
    """One punch as a JSON object.

    Fields mapped to ``None`` are omitted rather than sent as null — the bridge does
    not add keys to someone else's contract, and a stray null can fail validation on
    a strict API.
    """
    field_map = profile.field_map
    record: dict[str, Any] = {
        field_map.employee_code: punch.employee_code,
        field_map.punch_time: format_timestamp(punch.punch_time, profile),
    }

    # Sent only when the profile names a field for it AND the terminal actually had
    # a name. Omitted rather than sent empty: a blank name is not information, and
    # the receiving side already holds the authoritative one once the employee is
    # enrolled — this exists so an *unenrolled* punch can say who it was.
    if field_map.employee_name and punch.employee_name:
        record[field_map.employee_name] = punch.employee_name

    if field_map.punch_type:
        value = str(punch.punch_type)
        record[field_map.punch_type] = profile.value_map.punch_type.get(value, value)

    if field_map.verification_type:
        value = str(punch.verification_type)
        record[field_map.verification_type] = profile.value_map.verification_type.get(
            value, value
        )

    if field_map.device_serial and punch.device_serial_number:
        record[field_map.device_serial] = punch.device_serial_number

    if field_map.device_id:
        record[field_map.device_id] = punch.device_id

    if field_map.client_record_id:
        record[field_map.client_record_id] = punch.client_record_id

    return record


def build_body(
    records: list[dict[str, Any]],
    profile: HrmsProfile,
    device_serial: str | None = None,
) -> Any:
    """Wrap records in the batching shape the profile declares."""
    if profile.batch.mode is BatchMode.ENVELOPE:
        body: dict[str, Any] = {}

        # Batch-level fields sit at the top of the envelope, beside the records
        # array. Only emitted when the profile names a key for them, so an API that
        # does not want the serial never sees it.
        serial_field = profile.batch.envelope_device_serial_field
        if serial_field:
            body[serial_field] = device_serial

        # Dotted envelope paths let a profile express {"data": {"records": [...]}}.
        parts = profile.batch.envelope_path.split(".")
        cursor = body
        for part in parts[:-1]:
            cursor[part] = {}
            cursor = cursor[part]
        cursor[parts[-1]] = records
        return body
    if profile.batch.mode is BatchMode.SINGLE:
        return records[0] if records else {}
    return records


def iter_batches(
    punches: list[BiometricPunch], profile: HrmsProfile
) -> Iterator[list[BiometricPunch]]:
    """Split into request-sized chunks.

    ``SINGLE`` mode yields one punch at a time regardless of the configured size,
    since the API takes one record per request by definition.
    """
    size = 1 if profile.batch.mode is BatchMode.SINGLE else profile.batch.size
    for start in range(0, len(punches), size):
        yield punches[start : start + size]


def build_request_body(punches: list[BiometricPunch], profile: HrmsProfile) -> Any:
    # A batch is always read from one terminal, so the serial is the same for every
    # punch in it; take it from the first rather than threading it through the call.
    device_serial = punches[0].device_serial_number if punches else None
    return build_body([punch_to_record(p, profile) for p in punches], profile, device_serial)
