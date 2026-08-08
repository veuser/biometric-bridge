"""Sync API.

The endpoint names encode watermark semantics, so these tests are as much about the
*routing* being unambiguous as about the handlers working: a "Sync Now" button must
not be able to reach the operation that ignores the watermark.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.database.models import SyncRun, SyncState
from app.utils.datetime_utils import utcnow

DEVICE = {
    "name": "Hyderabad Exit",
    "ip_address": "172.20.200.25",
    "port": 4370,
    "role": "CHECK_OUT",
}


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "syncadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


@pytest.fixture
def device_id(admin: TestClient) -> int:
    return int(admin.post("/api/devices", json=DEVICE).json()["id"])


class TestEndpointSemantics:
    def test_the_two_operations_are_separate_endpoints(self, admin: TestClient) -> None:
        """Named after watermark behaviour, not after who clicked — so the
        watermark-ignoring operation cannot be reached by accident (RULE 5)."""
        spec = admin.get("/api/openapi.json").json()["paths"]
        assert "/api/sync/catch-up/{device_id}" in spec
        assert "/api/sync/manual-range/{device_id}" in spec

    def test_manual_range_requires_an_explicit_window(
        self, admin: TestClient, device_id: int
    ) -> None:
        assert admin.post(f"/api/sync/manual-range/{device_id}", json={}).status_code == 422

    def test_manual_range_rejects_naive_timestamps(
        self, admin: TestClient, device_id: int
    ) -> None:
        """A naive value would be read in some arbitrary zone and fetch the wrong
        window off the device."""
        response = admin.post(
            f"/api/sync/manual-range/{device_id}",
            json={"from_time": "2026-08-08T09:00:00", "to_time": "2026-08-08T18:00:00"},
        )
        assert response.status_code == 422

    def test_manual_range_rejects_an_inverted_window(
        self, admin: TestClient, device_id: int
    ) -> None:
        response = admin.post(
            f"/api/sync/manual-range/{device_id}",
            json={
                "from_time": "2026-08-08T18:00:00+05:30",
                "to_time": "2026-08-08T09:00:00+05:30",
            },
        )
        assert response.status_code == 400
        assert "before the end" in response.json()["detail"]

    def test_manual_range_rejects_a_future_window(
        self, admin: TestClient, device_id: int
    ) -> None:
        future = utcnow() + timedelta(days=2)
        response = admin.post(
            f"/api/sync/manual-range/{device_id}",
            json={
                "from_time": (future - timedelta(hours=1)).isoformat(),
                "to_time": future.isoformat(),
            },
        )
        assert response.status_code == 400
        assert "future" in response.json()["detail"]

    def test_manual_range_rejects_an_oversized_window(
        self, admin: TestClient, device_id: int
    ) -> None:
        now = utcnow()
        response = admin.post(
            f"/api/sync/manual-range/{device_id}",
            json={
                "from_time": (now - timedelta(days=90)).isoformat(),
                "to_time": now.isoformat(),
            },
        )
        assert response.status_code == 400
        assert "smaller pieces" in response.json()["detail"]


class TestGuards:
    def test_disabled_device_is_refused(self, admin: TestClient, device_id: int) -> None:
        admin.put(f"/api/devices/{device_id}/enabled", params={"enabled": False})
        response = admin.post(f"/api/sync/catch-up/{device_id}")
        assert response.status_code == 400
        assert "disabled" in response.json()["detail"]

    def test_unknown_device_is_refused(self, admin: TestClient) -> None:
        assert admin.post("/api/sync/catch-up/999").status_code == 400

    def test_a_held_lock_produces_409(self, admin: TestClient, device_id: int) -> None:
        """Concurrent syncs would push the same window twice."""
        with admin.app.state.db.session() as session:
            state = session.query(SyncState).filter_by(device_id=device_id).one()
            state.locked_at = utcnow()
            state.lock_owner = "pid:99999"

        response = admin.post(f"/api/sync/catch-up/{device_id}")
        assert response.status_code == 409
        assert "already running" in response.json()["detail"]


class TestStatus:
    def test_status_lists_every_device(self, admin: TestClient, device_id: int) -> None:
        body = admin.get("/api/sync/status").json()
        assert body["auto_sync_enabled"] is False
        assert len(body["devices"]) == 1
        assert body["devices"][0]["last_successful_push_at"] is None

    def test_status_reports_a_held_lock_as_running(
        self, admin: TestClient, device_id: int
    ) -> None:
        with admin.app.state.db.session() as session:
            state = session.query(SyncState).filter_by(device_id=device_id).one()
            state.locked_at = utcnow()
            state.lock_owner = "pid:1"
        assert admin.get("/api/sync/status").json()["devices"][0]["is_running"] is True

    def test_status_surfaces_clock_drift(self, admin: TestClient, device_id: int) -> None:
        """The reference terminals run ~150s slow; the operator has to see it."""
        with admin.app.state.db.session() as session:
            state = session.query(SyncState).filter_by(device_id=device_id).one()
            state.clock_offset_seconds = -154.0
        body = admin.get("/api/sync/status").json()
        assert "154 seconds behind" in body["devices"][0]["clock_warning"]


class TestHistory:
    def _seed(self, admin: TestClient, device_id: int, count: int = 5) -> None:
        from app.core.enums import SyncStatus, SyncTrigger

        with admin.app.state.db.session() as session:
            for index in range(count):
                session.add(
                    SyncRun(
                        device_id=device_id,
                        trigger=SyncTrigger.SCHEDULED if index % 2 else SyncTrigger.MANUAL_RANGE,
                        status=SyncStatus.SUCCESS if index % 2 else SyncStatus.FAILED,
                        started_at=utcnow() - timedelta(minutes=index),
                        completed_at=utcnow() - timedelta(minutes=index) + timedelta(seconds=3),
                        window_records=index,
                        records_success=index,
                    )
                )

    def test_history_is_paginated_and_newest_first(
        self, admin: TestClient, device_id: int
    ) -> None:
        self._seed(admin, device_id, 5)
        body = admin.get("/api/sync/history", params={"page_size": 2}).json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["items"][0]["started_at"] > body["items"][1]["started_at"]

    def test_history_can_be_filtered_by_status_and_trigger(
        self, admin: TestClient, device_id: int
    ) -> None:
        self._seed(admin, device_id, 5)
        assert admin.get("/api/sync/history", params={"run_status": "FAILED"}).json()["total"] == 3
        assert (
            admin.get("/api/sync/history", params={"trigger": "SCHEDULED"}).json()["total"] == 2
        )

    def test_history_includes_the_device_name_and_duration(
        self, admin: TestClient, device_id: int
    ) -> None:
        self._seed(admin, device_id, 1)
        item = admin.get("/api/sync/history").json()["items"][0]
        assert item["device_name"] == "Hyderabad Exit"
        assert item["duration_ms"] == 3000

    def test_history_never_exposes_punch_data(self, admin: TestClient, device_id: int) -> None:
        """RULE 1 — counts only, never the records themselves."""
        self._seed(admin, device_id, 1)
        text = admin.get("/api/sync/history").text
        for forbidden in ("employee_code", "employeeCode", "punch_time", "punchTime"):
            assert forbidden not in text


class TestAuthorization:
    def test_all_sync_endpoints_require_a_session(self, client: TestClient) -> None:
        assert client.get("/api/sync/status").status_code == 401
        assert client.get("/api/sync/history").status_code == 401
        assert client.post("/api/sync/catch-up").status_code == 401
        assert client.post("/api/sync/catch-up/1").status_code == 401
        assert client.post("/api/sync/manual-range/1", json={}).status_code == 401
