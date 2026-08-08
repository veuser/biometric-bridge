"""Record decoding and normalization.

The spike this project replaces read the punch-state field and labelled it a
verification method. These tests pin the corrected mapping so that regression cannot
come back quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.biometric.base import RawPunch
from app.biometric.mapping import decode_punch_type, decode_verification, describe_raw
from app.biometric.normalizer import make_client_record_id, normalize, normalize_many
from app.core.enums import DeviceRole, PunchType, VerificationType
from app.core.errors import InvalidRecordError

IST = ZoneInfo("Asia/Kolkata")


class TestFieldDecoding:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (0, VerificationType.PASSWORD),
            (1, VerificationType.FINGERPRINT),
            (2, VerificationType.CARD),
            (15, VerificationType.FACE),
        ],
    )
    def test_status_is_the_verification_mode(
        self, status: int, expected: VerificationType
    ) -> None:
        assert decode_verification(status) == expected

    def test_unknown_verification_degrades_rather_than_guessing(self) -> None:
        """Firmware variants differ; a wrong guess is worse than an honest UNKNOWN."""
        assert decode_verification(99) is VerificationType.UNKNOWN

    @pytest.mark.parametrize(
        ("punch", "expected"),
        [
            (0, PunchType.CHECK_IN),
            (1, PunchType.CHECK_OUT),
            (2, PunchType.BREAK_OUT),
            (3, PunchType.BREAK_IN),
            (4, PunchType.OT_IN),
            (5, PunchType.OT_OUT),
        ],
    )
    def test_punch_is_the_attendance_state(self, punch: int, expected: PunchType) -> None:
        assert decode_punch_type(punch, DeviceRole.FROM_DEVICE) == expected

    def test_the_two_fields_are_not_interchangeable(self) -> None:
        """The exact bug in the reference spike: punch read as a verification mode.

        With status=1/punch=1 the correct answer is FINGERPRINT + CHECK_OUT. The old
        code produced "Password" by decoding punch through the verification table.
        """
        assert decode_verification(1) is VerificationType.FINGERPRINT
        assert decode_punch_type(1, DeviceRole.FROM_DEVICE) is PunchType.CHECK_OUT

    def test_device_role_overrides_the_record(self) -> None:
        """The reference deployment has a dedicated entry and exit terminal."""
        assert decode_punch_type(1, DeviceRole.CHECK_IN) is PunchType.CHECK_IN
        assert decode_punch_type(0, DeviceRole.CHECK_OUT) is PunchType.CHECK_OUT

    def test_describe_raw_surfaces_the_integers_for_diagnosis(self) -> None:
        text = describe_raw(1, 1)
        assert "status=1" in text and "punch=1" in text
        assert "FINGERPRINT" in text and "CHECK_OUT" in text

    def test_describe_raw_flags_unrecognised_values(self) -> None:
        assert "unrecognised" in describe_raw(77, 88)


class TestNormalize:
    def _raw(self, **kw: object) -> RawPunch:
        defaults: dict[str, object] = {
            "employee_code": "IQ-1004",
            "timestamp": datetime(2026, 8, 8, 12, 7, 9),
            "status": 1,
            "punch": 1,
        }
        defaults.update(kw)
        return RawPunch(**defaults)  # type: ignore[arg-type]

    def _normalize(self, raw: RawPunch, role: DeviceRole = DeviceRole.CHECK_OUT) -> object:
        return normalize(raw, device_id=1, device_role=role, device_serial="ESSL1", tz=IST)

    def test_device_local_time_becomes_utc(self) -> None:
        punch = self._normalize(self._raw())
        assert punch.punch_time == datetime(2026, 8, 8, 6, 37, 9, tzinfo=UTC)  # type: ignore[attr-defined]

    def test_fields_are_decoded_correctly(self) -> None:
        punch = self._normalize(self._raw(status=1, punch=1))
        assert punch.verification_type is VerificationType.FINGERPRINT  # type: ignore[attr-defined]
        assert punch.punch_type is PunchType.CHECK_OUT  # type: ignore[attr-defined]

    def test_raw_integers_are_carried_through(self) -> None:
        punch = self._normalize(self._raw(status=7, punch=9))
        assert (punch.raw_status, punch.raw_punch) == (7, 9)  # type: ignore[attr-defined]

    def test_missing_employee_code_is_rejected(self) -> None:
        with pytest.raises(InvalidRecordError):
            self._normalize(self._raw(employee_code="   "))

    def test_an_already_aware_timestamp_is_rejected(self) -> None:
        """Would double-apply the timezone offset and silently shift the punch."""
        with pytest.raises(InvalidRecordError):
            self._normalize(self._raw(timestamp=datetime(2026, 8, 8, 12, 0, tzinfo=UTC)))

    def test_employee_code_is_trimmed(self) -> None:
        assert self._normalize(self._raw(employee_code=" IQ-1004 ")).employee_code == "IQ-1004"  # type: ignore[attr-defined]


class TestClientRecordId:
    def _id(self, **kw: object) -> str:
        defaults: dict[str, object] = {
            "device_serial": "ESSL1",
            "employee_code": "IQ-1004",
            "punch_time": datetime(2026, 8, 8, 6, 37, 9, tzinfo=UTC),
            "raw_punch": 1,
        }
        defaults.update(kw)
        return make_client_record_id(**defaults)  # type: ignore[arg-type]

    def test_is_deterministic_across_calls(self) -> None:
        """Retries and restarts must produce the same id or suppression breaks."""
        assert self._id() == self._id()

    @pytest.mark.parametrize(
        "change",
        [
            {"employee_code": "IQ-1103"},
            {"punch_time": datetime(2026, 8, 8, 6, 37, 10, tzinfo=UTC)},
            {"raw_punch": 0},
            {"device_serial": "ESSL2"},
        ],
    )
    def test_any_field_change_changes_the_id(self, change: dict[str, object]) -> None:
        assert self._id(**change) != self._id()


class TestNormalizeMany:
    def test_bad_records_are_counted_not_fatal(self) -> None:
        """One malformed record must not cost the whole window."""
        records = [
            RawPunch("IQ-1", datetime(2026, 8, 8, 12, 0), 1, 0),
            RawPunch("", datetime(2026, 8, 8, 12, 1), 1, 0),
            RawPunch("IQ-2", datetime(2026, 8, 8, 12, 2), 1, 0),
        ]
        result = normalize_many(
            records, device_id=1, device_role=DeviceRole.CHECK_IN, device_serial="S", tz=IST
        )
        assert len(result.punches) == 2
        assert result.invalid_count == 1

    def test_duplicate_records_are_collapsed(self) -> None:
        """Some firmware reports the same punch twice."""
        record = RawPunch("IQ-1", datetime(2026, 8, 8, 12, 0), 1, 0)
        result = normalize_many(
            [record, record, record],
            device_id=1,
            device_role=DeviceRole.CHECK_IN,
            device_serial="S",
            tz=IST,
        )
        assert len(result.punches) == 1

    def test_output_is_sorted_by_time(self) -> None:
        """The watermark is the max punch time, so ordering must be reliable."""
        records = [
            RawPunch("IQ-2", datetime(2026, 8, 8, 12, 5), 1, 0),
            RawPunch("IQ-1", datetime(2026, 8, 8, 12, 0), 1, 0),
        ]
        result = normalize_many(
            records, device_id=1, device_role=DeviceRole.CHECK_IN, device_serial="S", tz=IST
        )
        assert [p.employee_code for p in result.punches] == ["IQ-1", "IQ-2"]

    def test_empty_input_is_not_an_error(self) -> None:
        result = normalize_many(
            [], device_id=1, device_role=DeviceRole.CHECK_IN, device_serial="S", tz=IST
        )
        assert result.punches == [] and result.invalid_count == 0
