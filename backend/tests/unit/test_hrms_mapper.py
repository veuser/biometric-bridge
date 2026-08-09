"""Payload construction.

The governing rule is that the bridge adapts to the customer's contract and never
adds to it. These tests pin that: a field the profile does not name never appears in
the body, in any batching mode.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.biometric.normalizer import BiometricPunch
from app.core.enums import PunchType, VerificationType
from app.hrms.mapper import build_request_body, format_timestamp, iter_batches, punch_to_record
from app.hrms.profile import BatchMode, DateTimeFormat, HrmsProfile


def make_punch(**overrides: object) -> BiometricPunch:
    defaults: dict[str, object] = {
        "employee_code": "IQ-1004",
        "employee_name": "Manoj Settebathula",
        "punch_time": datetime(2026, 8, 8, 6, 37, 9, tzinfo=UTC),
        "punch_type": PunchType.CHECK_OUT,
        "verification_type": VerificationType.FINGERPRINT,
        "device_id": 2,
        "device_serial_number": "BJ2C184562186",
        "raw_status": 1,
        "raw_punch": 1,
        "client_record_id": "abc123",
    }
    defaults.update(overrides)
    return BiometricPunch(**defaults)  # type: ignore[arg-type]


def configured(**overrides: object) -> HrmsProfile:
    """A profile with a real endpoint path, so it passes the is_configured gate."""
    fields: dict[str, object] = {"attendance_path": "/api/attendance/punches"}
    fields.update(overrides)
    return HrmsProfile(**fields)  # type: ignore[arg-type]


class TestTimestampFormatting:
    def test_iso_offset_uses_the_output_timezone(self) -> None:
        profile = configured(output_timezone="Asia/Kolkata")
        assert format_timestamp(make_punch().punch_time, profile) == "2026-08-08T12:07:09+05:30"

    def test_iso_utc_z(self) -> None:
        profile = configured(datetime_format=DateTimeFormat.ISO8601_UTC_Z)
        assert format_timestamp(make_punch().punch_time, profile) == "2026-08-08T06:37:09Z"

    def test_custom_strftime_pattern(self) -> None:
        profile = configured(
            datetime_format=DateTimeFormat.CUSTOM,
            datetime_pattern="%d-%m-%Y %H:%M:%S",
            output_timezone="Asia/Kolkata",
        )
        assert format_timestamp(make_punch().punch_time, profile) == "08-08-2026 12:07:09"


class TestRecordShape:
    def test_uses_the_configured_field_names(self) -> None:
        profile = configured(
            field_map={"employee_code": "empCode", "punch_time": "ts", "punch_type": "kind"}
        )
        record = punch_to_record(make_punch(), profile)
        assert set(record) == {"empCode", "ts", "kind", "verificationType"}

    def test_fields_mapped_to_none_are_omitted_entirely(self) -> None:
        """Not sent as null — the bridge does not add keys to someone else's API,
        and a stray null can fail validation on a strict endpoint."""
        profile = configured(
            field_map={
                "employee_code": "employeeCode",
                "punch_time": "punchTime",
                "punch_type": None,
                "verification_type": None,
            }
        )
        record = punch_to_record(make_punch(), profile)
        assert set(record) == {"employeeCode", "punchTime"}

    def test_employee_name_is_never_sent(self) -> None:
        """The normalized punch carries no name, so none can leak by accident."""
        record = punch_to_record(make_punch(), configured())
        assert not any("name" in key.lower() for key in record)

    def test_device_serial_only_when_requested(self) -> None:
        assert "deviceSerial" not in punch_to_record(make_punch(), configured())
        profile = configured(field_map={"device_serial": "deviceSerial"})
        assert punch_to_record(make_punch(), profile)["deviceSerial"] == "BJ2C184562186"

    def test_client_record_id_only_when_the_contract_has_a_field_for_it(self) -> None:
        """The bridge never invents an idempotency field in someone else's API."""
        assert "clientRecordId" not in punch_to_record(make_punch(), configured())
        profile = configured(field_map={"client_record_id": "clientRecordId"})
        assert punch_to_record(make_punch(), profile)["clientRecordId"] == "abc123"

    def test_value_map_translates_enum_values(self) -> None:
        profile = configured(value_map={"punch_type": {"CHECK_OUT": "OUT", "CHECK_IN": "IN"}})
        assert punch_to_record(make_punch(), profile)["punchType"] == "OUT"

    def test_unmapped_values_pass_through_unchanged(self) -> None:
        profile = configured(value_map={"punch_type": {"CHECK_IN": "IN"}})
        assert punch_to_record(make_punch(), profile)["punchType"] == "CHECK_OUT"


class TestBatching:
    def test_array_mode_sends_a_bare_list(self) -> None:
        body = build_request_body([make_punch(), make_punch()], configured())
        assert isinstance(body, list) and len(body) == 2

    def test_envelope_mode_nests_under_the_configured_key(self) -> None:
        profile = configured(batch={"mode": BatchMode.ENVELOPE, "envelope_path": "records"})
        body = build_request_body([make_punch()], profile)
        assert list(body) == ["records"] and len(body["records"]) == 1

    def test_envelope_supports_a_nested_path(self) -> None:
        profile = configured(batch={"mode": BatchMode.ENVELOPE, "envelope_path": "data.items"})
        body = build_request_body([make_punch()], profile)
        assert len(body["data"]["items"]) == 1

    def test_single_mode_sends_one_object(self) -> None:
        profile = configured(batch={"mode": BatchMode.SINGLE})
        body = build_request_body([make_punch()], profile)
        assert isinstance(body, dict) and "employeeCode" in body

    def test_chunking_respects_batch_size(self) -> None:
        profile = configured(batch={"size": 2})
        batches = list(iter_batches([make_punch() for _ in range(5)], profile))
        assert [len(b) for b in batches] == [2, 2, 1]

    def test_single_mode_ignores_batch_size(self) -> None:
        """One record per request is the definition of the mode."""
        profile = configured(batch={"mode": BatchMode.SINGLE, "size": 100})
        batches = list(iter_batches([make_punch() for _ in range(3)], profile))
        assert [len(b) for b in batches] == [1, 1, 1]

    def test_empty_input_produces_an_empty_batch_list(self) -> None:
        assert list(iter_batches([], configured())) == []


class TestShippedTemplate:
    """The shipped profile must be a usable template, never a guess."""

    def test_template_parses_as_a_valid_profile(self) -> None:
        import json
        from pathlib import Path

        from app.hrms.profile import HrmsProfile

        path = Path(__file__).resolve().parents[2] / "app/hrms/profiles/default.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_comment", None)
        profile = HrmsProfile.model_validate(raw)
        assert profile.is_configured is False, (
            "the shipped template must carry the placeholder path so the bridge "
            "cannot be enabled against a guessed endpoint"
        )

    def test_template_sends_no_optional_field_by_default(self) -> None:
        """Defaults must not leak device ids or an invented idempotency key."""
        from app.hrms.profile import HrmsProfile

        field_map = HrmsProfile.default().field_map
        assert field_map.device_serial is None
        assert field_map.device_id is None
        assert field_map.client_record_id is None
