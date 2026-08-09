"""The employee name: where it comes from, where it goes, and where it must not.

The terminal is the only place a name exists at punch time — the attendance record
carries a user id and nothing else. It is carried through purely so a punch that
cannot be attributed can say *who* it was, which turns a rejection list into an
enrolment list.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.biometric.normalizer import BiometricPunch, normalize
from app.biometric.base import RawPunch
from app.core.enums import DeviceRole, PunchType, VerificationType
from app.hrms.mapper import build_request_body, punch_to_record
from tests.unit.test_hrms_mapper import configured, make_punch

from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class TestItSurvivesNormalization:
    def test_the_name_reaches_the_normalized_punch(self) -> None:
        raw = RawPunch(
            employee_code="IQ-1004",
            employee_name="Manoj Settebathula",
            timestamp=datetime(2026, 8, 8, 12, 7, 9),
            status=1,
            punch=1,
        )
        punch = normalize(
            raw, device_id=1, device_role=DeviceRole.CHECK_OUT, device_serial="X", tz=IST
        )
        assert punch.employee_name == "Manoj Settebathula"

    def test_a_nameless_user_is_still_a_valid_punch(self) -> None:
        """A terminal with no name for a user must not lose that user's attendance."""
        raw = RawPunch(
            employee_code="IQ-9999",
            employee_name=None,
            timestamp=datetime(2026, 8, 8, 12, 7, 9),
            status=1,
            punch=1,
        )
        punch = normalize(
            raw, device_id=1, device_role=DeviceRole.CHECK_OUT, device_serial="X", tz=IST
        )
        assert punch.employee_name is None
        assert punch.employee_code == "IQ-9999"


class TestTheLabel:
    def test_it_names_the_person_when_known(self) -> None:
        punch = make_punch(employee_code="IQ-1004", employee_name="Manoj Settebathula")
        assert punch.label == "IQ-1004 (Manoj Settebathula)"

    def test_it_falls_back_to_the_bare_code(self) -> None:
        punch = make_punch(employee_code="IQ-1004", employee_name=None)
        assert punch.label == "IQ-1004"


class TestOnTheWire:
    def test_it_is_omitted_unless_the_profile_asks_for_it(self) -> None:
        """A name is duplicate data to an API that already knows the employee, so it
        is not pushed into a contract that never asked for it."""
        record = punch_to_record(make_punch(), configured())
        assert "employeeName" not in record
        assert not any("name" in key.lower() for key in record)

    def test_it_is_sent_when_mapped(self) -> None:
        profile = configured(field_map={"employee_name": "employeeName"})
        record = punch_to_record(
            make_punch(employee_name="Manoj Settebathula"), profile
        )
        assert record["employeeName"] == "Manoj Settebathula"

    def test_a_missing_name_omits_the_key_rather_than_sending_null(self) -> None:
        """An empty name is not information, and a stray null fails validation on a
        strict API."""
        profile = configured(field_map={"employee_name": "employeeName"})
        record = punch_to_record(make_punch(employee_name=None), profile)
        assert "employeeName" not in record

    def test_the_shipped_profile_sends_it(self) -> None:
        import json
        from pathlib import Path

        from app.hrms.profile import HrmsProfile

        path = Path(__file__).resolve().parents[2] / "app" / "hrms" / "profiles" / "vectoredge-hrms.json"
        profile = HrmsProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))

        body = build_request_body([make_punch(employee_name="Manoj Settebathula")], profile)
        assert body["punches"][0]["employeeName"] == "Manoj Settebathula"
