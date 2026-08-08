"""HRMS client against a scripted fake HRMS."""

from __future__ import annotations

import pytest

from app.core.errors import (
    HrmsAuthenticationError,
    HrmsBadRequestError,
    HrmsNotConfiguredError,
    HrmsTimeoutError,
    HrmsUnavailableError,
)
from app.hrms.client import HrmsClient
from app.hrms.profile import AuthScheme, HrmsProfile
from tests.fakes.fake_hrms import FakeHrms
from tests.unit.test_hrms_mapper import configured, make_punch

API_KEY = "hrms-live-key-abc123456"


def client_for(hrms: FakeHrms, profile: HrmsProfile | None = None) -> HrmsClient:
    return HrmsClient(
        base_url="https://hrms.example.com",
        profile=profile or configured(),
        transport=hrms.transport(),
    )


class TestConfigurationGate:
    def test_unconfigured_profile_refuses_to_build_a_client(self) -> None:
        """The bridge will not guess an endpoint path."""
        with pytest.raises(HrmsNotConfiguredError):
            HrmsClient(base_url="https://hrms.example.com", profile=HrmsProfile.default())


class TestSuccessPath:
    def test_push_reports_processed_count(self) -> None:
        hrms = FakeHrms(response_body={"success": True, "processed": 2})
        profile = configured(response={"processed_count_path": "processed"})
        result = client_for(hrms, profile).push(
            [make_punch(), make_punch(client_record_id="x2")], api_key=API_KEY
        )
        assert result.processed == 2 and result.failed == 0

    def test_url_and_method_come_from_the_profile(self) -> None:
        hrms = FakeHrms()
        profile = configured(attendance_path="/v2/attendance/import", method="PUT")
        client_for(hrms, profile).push([make_punch()], api_key=API_KEY)
        assert hrms.last_request.url == "https://hrms.example.com/v2/attendance/import"
        assert hrms.last_request.method == "PUT"

    def test_empty_response_body_on_success_is_fine(self) -> None:
        """Plenty of APIs answer 204 with nothing at all."""
        hrms = FakeHrms(status_code=204, response_body=None)
        result = client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert result.status_code == 204

    def test_processed_defaults_to_batch_size_when_unreported(self) -> None:
        hrms = FakeHrms(response_body={"ok": True})
        result = client_for(hrms).push([make_punch(), make_punch()], api_key=API_KEY)
        assert result.processed == 2


class TestAuthentication:
    def test_header_scheme_uses_the_configured_name_and_template(self) -> None:
        hrms = FakeHrms()
        profile = configured(
            auth={"scheme": AuthScheme.HEADER, "header_name": "X-Api-Token",
                  "value_template": "Token {api_key}"}
        )
        client_for(hrms, profile).push([make_punch()], api_key=API_KEY)
        assert hrms.last_request.headers["x-api-token"] == f"Token {API_KEY}"

    def test_bearer_scheme(self) -> None:
        hrms = FakeHrms()
        profile = configured(auth={"scheme": AuthScheme.BEARER})
        client_for(hrms, profile).push([make_punch()], api_key=API_KEY)
        assert hrms.last_request.headers["authorization"] == f"Bearer {API_KEY}"

    def test_query_scheme(self) -> None:
        hrms = FakeHrms()
        profile = configured(auth={"scheme": AuthScheme.QUERY, "query_param": "key"})
        client_for(hrms, profile).push([make_punch()], api_key=API_KEY)
        assert f"key={API_KEY}" in hrms.last_request.url

    def test_tenant_header_is_sent_when_configured(self) -> None:
        hrms = FakeHrms()
        profile = configured(tenant={"header_name": "X-Tenant-Id"})
        client_for(hrms, profile).push([make_punch()], api_key=API_KEY, tenant="acme")
        assert hrms.last_request.headers["x-tenant-id"] == "acme"

    def test_no_tenant_header_when_not_configured(self) -> None:
        hrms = FakeHrms()
        client_for(hrms).push([make_punch()], api_key=API_KEY, tenant="acme")
        assert not any("tenant" in h for h in hrms.last_request.headers)


class TestErrorClassification:
    @pytest.mark.parametrize("code", [401, 403])
    def test_auth_failures_are_not_retryable(self, code: int) -> None:
        hrms = FakeHrms(status_code=code)
        with pytest.raises(HrmsAuthenticationError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert exc.value.retryable is False

    @pytest.mark.parametrize("code", [400, 422])
    def test_bad_request_is_not_retryable(self, code: int) -> None:
        hrms = FakeHrms(status_code=code, response_body={"error": "bad field"})
        with pytest.raises(HrmsBadRequestError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert exc.value.retryable is False

    @pytest.mark.parametrize("code", [500, 502, 503])
    def test_server_errors_are_retryable(self, code: int) -> None:
        hrms = FakeHrms(status_code=code)
        with pytest.raises(HrmsUnavailableError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert exc.value.retryable is True

    def test_rate_limit_is_retryable(self) -> None:
        hrms = FakeHrms(status_code=429)
        with pytest.raises(HrmsUnavailableError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert exc.value.retryable is True

    def test_timeout_is_retryable(self) -> None:
        hrms = FakeHrms(raise_timeout=True)
        with pytest.raises(HrmsTimeoutError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert exc.value.retryable is True

    def test_connection_failure_is_retryable(self) -> None:
        hrms = FakeHrms(raise_connect_error=True)
        with pytest.raises(HrmsUnavailableError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert exc.value.retryable is True

    def test_success_flag_false_in_body_is_a_failure(self) -> None:
        """Some APIs answer 200 with a failure flag."""
        hrms = FakeHrms(response_body={"success": False, "message": "employee not found"})
        profile = configured(
            response={"success_path": "success", "error_message_path": "message"}
        )
        with pytest.raises(HrmsBadRequestError, match="employee not found"):
            client_for(hrms, profile).push([make_punch()], api_key=API_KEY)

    def test_error_message_is_truncated_for_the_portal(self) -> None:
        """An HRMS error page can be a whole HTML document."""
        hrms = FakeHrms(status_code=400, response_body={"error": "x" * 5000})
        with pytest.raises(HrmsBadRequestError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert len(exc.value.message) < 400


class TestPartialAcceptance:
    """Only reachable when the profile declares a per-record results path."""

    def _profile(self) -> HrmsProfile:
        return configured(
            field_map={"client_record_id": "clientRecordId"},
            response={
                "results_path": "results",
                "result_id_path": "id",
                "result_ok_path": "accepted",
            },
        )

    def test_accepted_and_rejected_are_separated(self) -> None:
        hrms = FakeHrms(
            response_body={
                "results": [
                    {"id": "rec-a", "accepted": True},
                    {"id": "rec-b", "accepted": False},
                ]
            }
        )
        punches = [
            make_punch(client_record_id="rec-a"),
            make_punch(client_record_id="rec-b"),
        ]
        result = client_for(hrms, self._profile()).push(punches, api_key=API_KEY)
        assert result.accepted_ids == {"rec-a"}
        assert result.rejected_ids == {"rec-b"}
        assert result.is_partial is True

    def test_all_accepted_is_not_partial(self) -> None:
        hrms = FakeHrms(response_body={"results": [{"id": "rec-a", "accepted": True}]})
        result = client_for(hrms, self._profile()).push(
            [make_punch(client_record_id="rec-a")], api_key=API_KEY
        )
        assert result.is_partial is False

    def test_without_a_results_path_the_batch_is_all_or_nothing(self) -> None:
        hrms = FakeHrms(response_body={"results": [{"id": "rec-a", "accepted": False}]})
        result = client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert result.accepted_ids == set() and result.rejected_ids == set()


class TestTestConnection:
    def test_probe_uses_the_configured_endpoint(self) -> None:
        hrms = FakeHrms(status_code=200)
        profile = configured(test_connection={"path": "/api/ping", "method": "GET"})
        reachable, _ = client_for(hrms, profile).test_connection(api_key=API_KEY)
        assert reachable is True
        assert hrms.last_request.url.endswith("/api/ping")

    def test_no_probe_endpoint_refuses_to_fabricate_a_punch(self) -> None:
        """Sending an invented punch to prove connectivity would write fake
        attendance into a production HRMS."""
        hrms = FakeHrms()
        reachable, message = client_for(hrms).test_connection(api_key=API_KEY)
        assert reachable is False
        assert "without sending real attendance data" in message
        assert hrms.call_count == 0

    def test_rejected_key_is_reported_clearly(self) -> None:
        hrms = FakeHrms(status_code=401)
        profile = configured(test_connection={"path": "/api/ping"})
        reachable, message = client_for(hrms, profile).test_connection(api_key=API_KEY)
        assert reachable is False and "rejected the API key" in message

    def test_unreachable_hrms_reports_rather_than_raising(self) -> None:
        hrms = FakeHrms(raise_connect_error=True)
        profile = configured(test_connection={"path": "/api/ping"})
        reachable, message = client_for(hrms, profile).test_connection(api_key=API_KEY)
        assert reachable is False and "could not be reached" in message


class TestKeySecrecy:
    def test_the_key_never_appears_in_an_error_message(self) -> None:
        hrms = FakeHrms(status_code=400, response_body={"error": "bad"})
        with pytest.raises(HrmsBadRequestError) as exc:
            client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert API_KEY not in exc.value.message

    def test_the_key_never_appears_in_the_request_body(self) -> None:
        hrms = FakeHrms()
        client_for(hrms).push([make_punch()], api_key=API_KEY)
        assert API_KEY not in str(hrms.last_request.body)
