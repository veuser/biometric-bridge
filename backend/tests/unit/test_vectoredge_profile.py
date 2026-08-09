"""The shipped VectorEdge HRMS profile, checked against the contract it targets.

These assert the *wire shape* the HRMS controller expects
(``HRMS.Api/Controllers/Attendence/BiometricAttendanceController.cs``). The two
codebases cannot import each other, so this file is the seam: if somebody edits the
profile and breaks the contract, it fails here rather than in production with punches
silently rejected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.enums import PunchType, VerificationType
from app.hrms.client import PushResult
from app.hrms.mapper import build_request_body
from app.hrms.profile import BatchMode, HrmsProfile

from tests.unit.test_sync_engine import make_punch  # shared punch factory

PROFILE_PATH = Path(__file__).resolve().parents[2] / "app" / "hrms" / "profiles" / "vectoredge-hrms.json"


@pytest.fixture(scope="module")
def profile() -> HrmsProfile:
    raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    # Strip the documentation keys the JSON carries for operators.
    raw.pop("_comment", None)
    raw.get("tenant", {}).pop("_comment", None)
    return HrmsProfile.model_validate(raw)


class TestProfileShape:
    def test_it_is_configured_unlike_the_template(self, profile: HrmsProfile) -> None:
        """default.json ships a placeholder; this one is a real, usable contract."""
        assert profile.is_configured
        assert profile.attendance_path == "/api/v1/attendence/biometric"

    def test_auth_header_matches_the_filter(self, profile: HrmsProfile) -> None:
        # IntegrationApiKeyAuthorize reads exactly this header name.
        assert profile.auth.header_name == "api_key"
        assert profile.auth.value_template == "{api_key}"

    def test_no_tenant_is_asserted(self, profile: HrmsProfile) -> None:
        """The tenant is resolved server-side from the key; the bridge cannot claim one."""
        assert profile.tenant.header_name is None
        assert profile.tenant.query_param is None

    def test_verification_type_is_not_sent(self, profile: HrmsProfile) -> None:
        """HRMS has no column for it, and status=4 is unidentified on this hardware."""
        assert profile.field_map.verification_type is None


class TestRequestBody:
    def _punch(self, **kwargs):
        defaults = dict(
            employee_code="IQ-1004",
            punch_time=datetime(2026, 8, 8, 6, 37, 9, tzinfo=timezone.utc),
            punch_type=PunchType.CHECK_OUT,
            verification_type=VerificationType.UNKNOWN,
            device_serial_number="BJ2C184562186",
            client_record_id="abc123",
        )
        defaults.update(kwargs)
        return make_punch(**defaults)

    def test_body_matches_the_controller_contract(self, profile: HrmsProfile) -> None:
        body = build_request_body([self._punch()], profile)

        assert body == {
            "deviceSerialNumber": "BJ2C184562186",
            "punches": [
                {
                    "employeeCode": "IQ-1004",
                    "punchTimeUtc": "2026-08-08T06:37:09Z",
                    "punchType": "OUT",
                    "sourceRef": "abc123",
                }
            ],
        }

    def test_no_date_or_wall_clock_is_sent(self, profile: HrmsProfile) -> None:
        """The instant is the ONLY temporal value on the wire.

        HRMS derives the attendance day and the hour/minute/second from it using the
        tenant timezone. Sending our own would recreate the web check-in defect,
        where the client supplies both and the read path computes the day
        differently — so they disagree either side of midnight.
        """
        record = build_request_body([self._punch()], profile)["punches"][0]
        assert set(record) == {"employeeCode", "punchTimeUtc", "punchType", "sourceRef"}

    def test_check_in_maps_to_in(self, profile: HrmsProfile) -> None:
        body = build_request_body([self._punch(punch_type=PunchType.CHECK_IN)], profile)
        assert body["punches"][0]["punchType"] == "IN"

    def test_batching_is_an_envelope(self, profile: HrmsProfile) -> None:
        assert profile.batch.mode is BatchMode.ENVELOPE
        assert profile.batch.envelope_path == "punches"
        assert profile.batch.envelope_device_serial_field == "deviceSerialNumber"


class TestResponseInterpretation:
    """The half of the contract that decides whether the watermark may move."""

    def _interpret(self, profile: HrmsProfile, results: list[dict]) -> PushResult:
        import httpx

        from app.hrms.client import HrmsClient

        client = HrmsClient(base_url="http://hrms.test", profile=profile)
        response = httpx.Response(
            200,
            json={"inserted": 0, "duplicate": 0, "failed": 0, "results": results},
            request=httpx.Request("POST", "http://hrms.test"),
        )
        punches = [make_punch(client_record_id=r["sourceRef"]) for r in results]
        return client._interpret(response, punches, 5)  # noqa: SLF001

    def test_inserted_and_duplicate_both_count_as_accepted(self, profile: HrmsProfile) -> None:
        """A duplicate means the row is already in HRMS. That is success."""
        result = self._interpret(
            profile,
            [
                {"sourceRef": "a", "status": "Inserted"},
                {"sourceRef": "b", "status": "Duplicate"},
            ],
        )
        assert result.accepted_ids == {"a", "b"}
        assert result.rejected_ids == set()

    def test_unknown_employee_is_rejected_but_terminal(self, profile: HrmsProfile) -> None:
        result = self._interpret(profile, [{"sourceRef": "a", "status": "UnknownEmployee"}])
        assert result.rejected_ids == {"a"}
        assert result.terminal_ids == {"a"}

    def test_an_unrecognised_status_is_rejected_and_retryable(self, profile: HrmsProfile) -> None:
        """Unknown statuses must NOT be assumed final.

        Holding the watermark for a status we do not understand is the recoverable
        mistake; advancing past a record that was never stored loses it silently.
        """
        result = self._interpret(profile, [{"sourceRef": "a", "status": "SomethingNew"}])
        assert result.rejected_ids == {"a"}
        assert result.terminal_ids == set()
