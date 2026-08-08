"""HRMS configuration API.

The recurring assertion is that the API key goes in and never comes back out —
through any endpoint, in any field, at any point.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

API_KEY = "hrms-live-key-abc123456"

CONFIG = {
    "base_url": "https://hrms.example.com",
    "api_key": API_KEY,
    "profile": {"attendance_path": "/api/attendance/punches"},
    "enabled": True,
}


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "hrmsadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


class TestConfigLifecycle:
    def test_unconfigured_returns_404(self, admin: TestClient) -> None:
        assert admin.get("/api/hrms/config").status_code == 404

    def test_save_and_read_back(self, admin: TestClient) -> None:
        assert admin.put("/api/hrms/config", json=CONFIG).status_code == 200
        body = admin.get("/api/hrms/config").json()
        assert body["base_url"] == "https://hrms.example.com"
        assert body["enabled"] is True
        assert body["profile_configured"] is True

    def test_trailing_slash_is_normalised(self, admin: TestClient) -> None:
        admin.put("/api/hrms/config", json={**CONFIG, "base_url": "https://hrms.example.com/"})
        assert admin.get("/api/hrms/config").json()["base_url"] == "https://hrms.example.com"

    def test_profile_round_trips(self, admin: TestClient) -> None:
        profile = {
            "attendance_path": "/v2/punches",
            "method": "PUT",
            "batch": {"mode": "envelope", "envelope_path": "data.records", "size": 50},
            "field_map": {"employee_code": "empId", "punch_time": "ts", "punch_type": None},
            "value_map": {"punch_type": {"CHECK_IN": "IN"}},
        }
        admin.put("/api/hrms/config", json={**CONFIG, "profile": profile})
        stored = admin.get("/api/hrms/config").json()["profile"]
        assert stored["attendance_path"] == "/v2/punches"
        assert stored["batch"]["envelope_path"] == "data.records"
        assert stored["field_map"]["punch_type"] is None


class TestApiKeySecrecy:
    def test_key_is_never_returned_by_any_endpoint(self, admin: TestClient) -> None:
        save = admin.put("/api/hrms/config", json=CONFIG)
        assert API_KEY not in save.text
        assert API_KEY not in admin.get("/api/hrms/config").text

    def test_only_a_masked_hint_is_exposed(self, admin: TestClient) -> None:
        admin.put("/api/hrms/config", json=CONFIG)
        body = admin.get("/api/hrms/config").json()
        assert body["api_key_set"] is True
        assert body["api_key_masked"].endswith(API_KEY[-4:])
        assert API_KEY[:-4] not in body["api_key_masked"]

    def test_omitting_the_key_on_edit_keeps_the_stored_one(self, admin: TestClient) -> None:
        """The form cannot pre-fill a write-only field, so omission must not wipe it."""
        admin.put("/api/hrms/config", json=CONFIG)
        payload = {k: v for k, v in CONFIG.items() if k != "api_key"}
        admin.put("/api/hrms/config", json={**payload, "base_url": "https://hrms2.example.com"})
        body = admin.get("/api/hrms/config").json()
        assert body["api_key_set"] is True
        assert body["base_url"] == "https://hrms2.example.com"

    def test_saved_key_is_redacted_from_the_log(self, admin: TestClient) -> None:
        """The main remaining control on the key, since encryption at rest is off."""
        from app.core.logging import redact

        admin.put("/api/hrms/config", json=CONFIG)
        assert API_KEY not in redact(f"calling hrms with {API_KEY}")


class TestGuards:
    def test_http_base_url_is_refused_by_default(self, admin: TestClient) -> None:
        """The API key would otherwise cross the network in clear text."""
        response = admin.put(
            "/api/hrms/config", json={**CONFIG, "base_url": "http://hrms.example.com"}
        )
        assert response.status_code == 400
        assert "HTTPS" in response.json()["detail"]

    def test_http_can_be_allowed_explicitly_for_a_lab(self, admin: TestClient) -> None:
        response = admin.put(
            "/api/hrms/config",
            json={
                **CONFIG,
                "base_url": "http://hrms.local",
                "allow_insecure_http": True,
            },
        )
        assert response.status_code == 200

    def test_cannot_enable_sync_with_the_placeholder_path(self, admin: TestClient) -> None:
        """The bridge refuses to guess an API contract."""
        response = admin.put(
            "/api/hrms/config", json={**CONFIG, "profile": {}}
        )
        assert response.status_code == 400
        assert "will not guess" in response.json()["detail"]

    def test_cannot_enable_sync_without_a_key(self, admin: TestClient) -> None:
        payload = {k: v for k, v in CONFIG.items() if k != "api_key"}
        response = admin.put("/api/hrms/config", json=payload)
        assert response.status_code == 400
        assert "API key is required" in response.json()["detail"]

    def test_can_save_a_draft_while_disabled(self, admin: TestClient) -> None:
        """Configuration is built up over several saves before being switched on."""
        response = admin.put(
            "/api/hrms/config",
            json={"base_url": "https://hrms.example.com", "enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["profile_configured"] is False

    def test_bad_base_url_is_rejected(self, admin: TestClient) -> None:
        response = admin.put("/api/hrms/config", json={**CONFIG, "base_url": "hrms.example.com"})
        assert response.status_code == 422

    def test_get_endpoint_must_use_a_write_method(self, admin: TestClient) -> None:
        response = admin.put(
            "/api/hrms/config",
            json={**CONFIG, "profile": {"attendance_path": "/x", "method": "GET"}},
        )
        assert response.status_code == 422


class TestTestConnection:
    def test_reports_unconfigured_rather_than_failing(self, admin: TestClient) -> None:
        body = admin.post("/api/hrms/test-connection").json()
        assert body["reachable"] is False
        assert "not been configured" in body["message"]

    def test_without_a_probe_endpoint_it_refuses_to_send_fake_data(
        self, admin: TestClient
    ) -> None:
        admin.put("/api/hrms/config", json=CONFIG)
        body = admin.post("/api/hrms/test-connection").json()
        assert body["reachable"] is False
        assert "without sending real attendance data" in body["message"]


class TestAuthorization:
    def test_endpoints_require_a_session(self, client: TestClient) -> None:
        assert client.get("/api/hrms/config").status_code == 401
        assert client.put("/api/hrms/config", json=CONFIG).status_code == 401
        assert client.post("/api/hrms/test-connection").status_code == 401
