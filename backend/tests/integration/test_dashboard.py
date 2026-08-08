"""Dashboard.

Every figure comes from sync-run metadata; no punch is stored to produce a statistic.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.enums import SyncStatus, SyncTrigger
from app.database.models import SyncRun, SyncState
from app.utils.datetime_utils import utcnow


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "dashadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


def test_fresh_install_is_honest_about_being_idle(admin: TestClient) -> None:
    """A green dashboard on an unconfigured bridge would be a lie."""
    body = admin.get("/api/dashboard").json()
    assert body["devices"]["total"] == 0
    assert body["hrms"]["configured"] is False
    assert any("not enabled" in w for w in body["warnings"])
    assert any("switched off" in w for w in body["warnings"])


def test_device_counts(admin: TestClient) -> None:
    admin.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.1", "port": 4370})
    admin.post("/api/devices", json={"name": "B", "ip_address": "10.0.0.2", "port": 4370})
    device_id = admin.get("/api/devices").json()[1]["id"]
    admin.put(f"/api/devices/{device_id}/enabled", params={"enabled": False})

    devices = admin.get("/api/dashboard").json()["devices"]
    assert devices == {
        "total": 2,
        "enabled": 1,
        "disabled": 1,
        "with_clock_warning": 0,
        "running": 0,
    }


def test_clock_drift_is_surfaced_as_a_warning(admin: TestClient) -> None:
    """The reference terminals run ~150s slow, which shifts every punch."""
    admin.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.1", "port": 4370})
    with admin.app.state.db.session() as session:
        session.query(SyncState).one().clock_offset_seconds = -154.0

    body = admin.get("/api/dashboard").json()
    assert body["devices"]["with_clock_warning"] == 1
    assert any("154 seconds behind" in w for w in body["warnings"])


def test_activity_counts_come_from_run_metadata(admin: TestClient) -> None:
    admin.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.1", "port": 4370})
    device_id = admin.get("/api/devices").json()[0]["id"]

    with admin.app.state.db.session() as session:
        session.add(
            SyncRun(
                device_id=device_id,
                trigger=SyncTrigger.SCHEDULED,
                status=SyncStatus.SUCCESS,
                completed_at=utcnow(),
                window_records=10,
                records_success=10,
            )
        )
        session.add(
            SyncRun(
                device_id=device_id,
                trigger=SyncTrigger.SCHEDULED,
                status=SyncStatus.HRMS_UNAVAILABLE,
                completed_at=utcnow(),
                error_message="HRMS is unavailable (HTTP 503).",
            )
        )

    body = admin.get("/api/dashboard").json()
    assert body["activity"]["runs"] == 2
    assert body["activity"]["records_pushed"] == 10
    assert body["activity"]["failed_runs"] == 1
    assert body["last_error"] == "HRMS is unavailable (HTTP 503)."


def test_activity_ignores_runs_older_than_a_day(admin: TestClient) -> None:
    admin.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.1", "port": 4370})
    device_id = admin.get("/api/devices").json()[0]["id"]
    with admin.app.state.db.session() as session:
        session.add(
            SyncRun(
                device_id=device_id,
                trigger=SyncTrigger.SCHEDULED,
                status=SyncStatus.SUCCESS,
                started_at=utcnow() - timedelta(days=3),
                completed_at=utcnow() - timedelta(days=3),
                records_success=99,
            )
        )
    assert admin.get("/api/dashboard").json()["activity"]["runs"] == 0


def test_scheduler_state_is_reported(admin: TestClient) -> None:
    body = admin.get("/api/dashboard").json()
    assert body["scheduler"]["running"] is True
    assert body["scheduler"]["auto_sync_enabled"] is False
    assert body["scheduler"]["next_run_at"] is None


def test_dashboard_never_exposes_punch_data_or_the_api_key(admin: TestClient) -> None:
    admin.put(
        "/api/hrms/config",
        json={
            "base_url": "https://hrms.example.com",
            "api_key": "hrms-live-key-abc123456",
            "profile": {"attendance_path": "/api/punches"},
            "enabled": True,
        },
    )
    text = admin.get("/api/dashboard").text
    assert "hrms-live-key-abc123456" not in text
    for forbidden in ("employeeCode", "punchTime", "employee_code"):
        assert forbidden not in text


def test_dashboard_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/dashboard").status_code == 401
