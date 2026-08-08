"""Device management API."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.biometric.base import ConnectionTestResult, DeviceInfo
from app.database.models import Device, SyncState

ENTRY = {
    "name": "Hyderabad Entry",
    "ip_address": "172.20.200.26",
    "port": 4370,
    "location": "Hyderabad Office",
    "role": "CHECK_IN",
}
EXIT = {
    "name": "Hyderabad Exit",
    "ip_address": "172.20.200.25",
    "port": 4370,
    "role": "CHECK_OUT",
}


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "deviceadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


class TestCrud:
    def test_create_and_list(self, admin: TestClient) -> None:
        assert admin.post("/api/devices", json=ENTRY).status_code == 201
        assert admin.post("/api/devices", json=EXIT).status_code == 201
        devices = admin.get("/api/devices").json()
        assert [d["role"] for d in devices] == ["CHECK_IN", "CHECK_OUT"]

    def test_creating_a_device_creates_its_sync_state(self, admin: TestClient) -> None:
        """Each device owns its watermark (RULE 4); no sync path should have to
        cope with a missing state row."""
        device_id = admin.post("/api/devices", json=ENTRY).json()["id"]
        with admin.app.state.db.session() as session:
            state = session.query(SyncState).filter_by(device_id=device_id).one()
            assert state.last_successful_push_at is None

    def test_duplicate_endpoint_is_rejected(self, admin: TestClient) -> None:
        """Two rows for one terminal would double-push every punch."""
        admin.post("/api/devices", json=ENTRY)
        response = admin.post("/api/devices", json=ENTRY)
        assert response.status_code == 400
        assert "already configured" in response.json()["detail"]

    def test_invalid_ip_is_rejected_at_the_form(self, admin: TestClient) -> None:
        response = admin.post("/api/devices", json={**ENTRY, "ip_address": "not-an-ip"})
        assert response.status_code == 422

    def test_unknown_timezone_is_rejected(self, admin: TestClient) -> None:
        response = admin.post("/api/devices", json={**ENTRY, "timezone": "Mars/Base"})
        assert response.status_code == 422

    def test_per_device_timezone_is_accepted(self, admin: TestClient) -> None:
        """Multi-site deployments can span timezones."""
        response = admin.post("/api/devices", json={**ENTRY, "timezone": "Asia/Kolkata"})
        assert response.status_code == 201
        assert response.json()["timezone"] == "Asia/Kolkata"

    def test_update(self, admin: TestClient) -> None:
        device_id = admin.post("/api/devices", json=ENTRY).json()["id"]
        response = admin.put(
            f"/api/devices/{device_id}", json={**ENTRY, "name": "Renamed", "role": "CHECK_OUT"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert response.json()["role"] == "CHECK_OUT"

    def test_enable_and_disable(self, admin: TestClient) -> None:
        device_id = admin.post("/api/devices", json=ENTRY).json()["id"]
        response = admin.put(f"/api/devices/{device_id}/enabled", params={"enabled": False})
        assert response.json()["enabled"] is False

    def test_delete_cascades_to_sync_state(self, admin: TestClient) -> None:
        device_id = admin.post("/api/devices", json=ENTRY).json()["id"]
        assert admin.delete(f"/api/devices/{device_id}").status_code == 204
        with admin.app.state.db.session() as session:
            assert session.query(Device).count() == 0
            assert session.query(SyncState).count() == 0

    def test_missing_device_is_404(self, admin: TestClient) -> None:
        assert admin.get("/api/devices/999").status_code == 404
        assert admin.delete("/api/devices/999").status_code == 404


class TestCommKeySecrecy:
    def test_comm_key_is_never_returned(self, admin: TestClient) -> None:
        response = admin.post("/api/devices", json={**ENTRY, "comm_key": "987654"})
        assert response.status_code == 201
        body = response.json()
        assert "987654" not in str(body)
        assert body["comm_key_set"] is True

    def test_comm_key_absent_from_list_and_detail(self, admin: TestClient) -> None:
        device_id = admin.post("/api/devices", json={**ENTRY, "comm_key": "987654"}).json()["id"]
        assert "987654" not in admin.get("/api/devices").text
        assert "987654" not in admin.get(f"/api/devices/{device_id}").text

    def test_editing_without_the_key_does_not_wipe_it(self, admin: TestClient) -> None:
        """The form cannot pre-fill a write-only field, so an omitted key must mean
        "leave it alone" — otherwise every edit silently breaks the connection."""
        device_id = admin.post("/api/devices", json={**ENTRY, "comm_key": "987654"}).json()["id"]
        admin.put(f"/api/devices/{device_id}", json={**ENTRY, "name": "Renamed"})
        assert admin.get(f"/api/devices/{device_id}").json()["comm_key_set"] is True

    def test_non_numeric_comm_key_is_rejected_at_the_form(self, admin: TestClient) -> None:
        """Caught alongside the IP, not later as a puzzling sync failure."""
        assert admin.post("/api/devices", json={**ENTRY, "comm_key": "abc"}).status_code == 422
        assert (
            admin.post(
                "/api/devices/test-connection",
                json={"ip_address": "10.255.255.1", "comm_key": "abc"},
            ).status_code
            == 422
        )


class TestTestConnection:
    def test_unsaved_device_can_be_probed(
        self, admin: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The add/edit form probes before saving a possibly-wrong configuration."""
        from app.biometric import essl_client

        def fake_test(self: Any) -> ConnectionTestResult:
            return ConnectionTestResult(
                reachable=True,
                message="Connected in 12 ms.",
                info=DeviceInfo(serial_number="ESSL123456", user_count=42, record_count=1200),
                clock_offset_seconds=3.0,
            )

        monkeypatch.setattr(essl_client.EsslBiometricDevice, "test_connection", fake_test)
        response = admin.post(
            "/api/devices/test-connection", json={"ip_address": "172.20.200.26"}
        )
        body = response.json()
        assert body["reachable"] is True
        assert body["info"]["serial_number"] == "ESSL123456"
        assert body["clock_warning"] is None

    def test_clock_drift_is_reported(
        self, admin: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drift silently shifts every punch, so it must be visible before go-live."""
        from app.biometric import essl_client

        def drifted(self: Any) -> ConnectionTestResult:
            return ConnectionTestResult(
                reachable=True, message="ok", info=DeviceInfo(), clock_offset_seconds=600.0
            )

        monkeypatch.setattr(essl_client.EsslBiometricDevice, "test_connection", drifted)
        body = admin.post(
            "/api/devices/test-connection", json={"ip_address": "172.20.200.26"}
        ).json()
        assert "600 seconds ahead of" in body["clock_warning"]

    def test_unreachable_device_returns_a_diagnosis_not_a_500(self, admin: TestClient) -> None:
        """An operator pressing "Test Connection" wants an answer, not a stack trace."""
        response = admin.post(
            "/api/devices/test-connection",
            json={"ip_address": "10.255.255.1", "port": 4370, "timeout_seconds": 1},
        )
        assert response.status_code == 200
        assert response.json()["reachable"] is False

    def test_saved_device_probe_404s_when_missing(self, admin: TestClient) -> None:
        assert admin.post("/api/devices/999/test-connection").status_code == 404


class TestAuthorization:
    def test_all_device_endpoints_require_a_session(self, client: TestClient) -> None:
        assert client.get("/api/devices").status_code == 401
        assert client.post("/api/devices", json=ENTRY).status_code == 401
        assert client.delete("/api/devices/1").status_code == 401
        probe = client.post("/api/devices/test-connection", json={"ip_address": "1.2.3.4"})
        assert probe.status_code == 401
