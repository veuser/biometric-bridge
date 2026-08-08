"""Scheduler and sync settings."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.enums import SyncStatus, SyncTrigger
from app.database.database import DatabaseManager
from app.database.models import SyncRun
from app.scheduler.scheduler import (
    RETENTION_JOB_ID,
    SYNC_JOB_ID,
    SyncScheduler,
    interval_warning,
)
from app.services.settings_service import SettingsService, SyncSettings
from app.services.sync_service import SyncOutcome
from app.utils.datetime_utils import utcnow


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "schedadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


class TestJobRegistration:
    """AsyncIOScheduler.start() requires a running event loop, so these are async."""

    async def test_no_sync_job_while_auto_sync_is_off(self, db: DatabaseManager) -> None:
        """Nothing should run against a bridge an operator has not switched on."""
        scheduler = SyncScheduler(db)
        scheduler.start()
        try:
            assert scheduler._scheduler.get_job(SYNC_JOB_ID) is None
        finally:
            scheduler.shutdown()

    async def test_sync_job_appears_once_enabled(self, db: DatabaseManager) -> None:
        with db.session() as session:
            SettingsService().save(
                session, SyncSettings(auto_sync_enabled=True, sync_interval_minutes=5)
            )
        scheduler = SyncScheduler(db)
        scheduler.start()
        try:
            job = scheduler._scheduler.get_job(SYNC_JOB_ID)
            assert job is not None
            assert job.trigger.interval == timedelta(minutes=5)
        finally:
            scheduler.shutdown()

    async def test_overlapping_cycles_are_prevented(self, db: DatabaseManager) -> None:
        """A cycle that overruns its interval must not start a second one."""
        with db.session() as session:
            SettingsService().save(session, SyncSettings(auto_sync_enabled=True))
        scheduler = SyncScheduler(db)
        scheduler.start()
        try:
            job = scheduler._scheduler.get_job(SYNC_JOB_ID)
            assert job is not None
            assert job.max_instances == 1
            assert job.coalesce is True, (
                "after a long outage, resume with one run rather than a backlog"
            )
        finally:
            scheduler.shutdown()

    async def test_retention_job_is_always_registered(self, db: DatabaseManager) -> None:
        scheduler = SyncScheduler(db)
        scheduler.start()
        try:
            assert scheduler._scheduler.get_job(RETENTION_JOB_ID) is not None
        finally:
            scheduler.shutdown()

    async def test_reload_applies_a_new_interval_without_a_restart(
        self, db: DatabaseManager
    ) -> None:
        scheduler = SyncScheduler(db)
        scheduler.start()
        try:
            with db.session() as session:
                SettingsService().save(
                    session, SyncSettings(auto_sync_enabled=True, sync_interval_minutes=15)
                )
            scheduler.reload()
            job = scheduler._scheduler.get_job(SYNC_JOB_ID)
            assert job is not None and job.trigger.interval == timedelta(minutes=15)
        finally:
            scheduler.shutdown()

    async def test_disabling_removes_the_job(self, db: DatabaseManager) -> None:
        with db.session() as session:
            SettingsService().save(session, SyncSettings(auto_sync_enabled=True))
        scheduler = SyncScheduler(db)
        scheduler.start()
        try:
            assert scheduler._scheduler.get_job(SYNC_JOB_ID) is not None
            with db.session() as session:
                SettingsService().save(session, SyncSettings(auto_sync_enabled=False))
            scheduler.reload()
            assert scheduler._scheduler.get_job(SYNC_JOB_ID) is None
        finally:
            scheduler.shutdown()


class TestCycleResilience:
    async def test_a_failing_cycle_does_not_kill_the_scheduler(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception escaping the job would be swallowed and the operator would
        see a scheduler that silently stopped working."""
        scheduler = SyncScheduler(db)

        def explode() -> list[SyncOutcome]:
            raise RuntimeError("device layer blew up")

        monkeypatch.setattr(scheduler, "_run_cycle_blocking", explode)
        await scheduler._run_cycle()  # must not raise

    async def test_retention_failure_is_contained(
        self, db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scheduler = SyncScheduler(db)

        def explode() -> int:
            raise RuntimeError("db gone")

        monkeypatch.setattr(scheduler, "_prune_history_blocking", explode)
        await scheduler._prune_history()


class TestRetention:
    def test_old_runs_are_pruned_and_recent_ones_kept(self, db: DatabaseManager) -> None:
        from app.database.models import Device

        with db.session() as session:
            device = Device(name="A", ip_address="10.0.0.1", port=4370)
            session.add(device)
            session.flush()
            SettingsService().save(session, SyncSettings(sync_run_retention_days=30))
            session.add(
                SyncRun(
                    device_id=device.id,
                    trigger=SyncTrigger.SCHEDULED,
                    status=SyncStatus.SUCCESS,
                    started_at=utcnow() - timedelta(days=200),
                )
            )
            session.add(
                SyncRun(
                    device_id=device.id,
                    trigger=SyncTrigger.SCHEDULED,
                    status=SyncStatus.SUCCESS,
                    started_at=utcnow() - timedelta(days=1),
                )
            )

        assert SyncScheduler(db)._prune_history_blocking() == 1
        with db.session() as session:
            assert session.query(SyncRun).count() == 1


class TestIntervalWarning:
    def test_short_interval_with_devices_warns(self) -> None:
        """Every cycle transfers each device's whole buffer — ~7.5s measured."""
        warning = interval_warning(SyncSettings(sync_interval_minutes=1), device_count=2)
        assert warning is not None and "little headroom" in warning

    def test_five_minutes_with_two_devices_is_fine(self) -> None:
        assert interval_warning(SyncSettings(sync_interval_minutes=5), device_count=2) is None

    def test_no_warning_without_devices(self) -> None:
        assert interval_warning(SyncSettings(sync_interval_minutes=1), device_count=0) is None


class TestSettingsApi:
    def test_defaults_are_returned(self, admin: TestClient) -> None:
        body = admin.get("/api/settings").json()
        assert body["auto_sync_enabled"] is False
        assert body["sync_interval_minutes"] == 5
        assert body["sync_overlap_seconds"] == 60

    def test_settings_round_trip(self, admin: TestClient) -> None:
        payload = {
            **admin.get("/api/settings").json(),
            "auto_sync_enabled": True,
            "sync_interval_minutes": 15,
            "sync_overlap_seconds": 120,
        }
        payload.pop("interval_warning", None)
        assert admin.put("/api/settings", json=payload).status_code == 200

        body = admin.get("/api/settings").json()
        assert body["auto_sync_enabled"] is True
        assert body["sync_interval_minutes"] == 15

    def test_saving_reloads_the_scheduler_in_place(self, admin: TestClient) -> None:
        """A changed interval must take effect without restarting the service."""
        payload = {**admin.get("/api/settings").json(), "auto_sync_enabled": True}
        payload.pop("interval_warning", None)
        admin.put("/api/settings", json=payload)

        scheduler = admin.app.state.scheduler
        assert scheduler._scheduler.get_job(SYNC_JOB_ID) is not None

    def test_out_of_range_values_are_rejected(self, admin: TestClient) -> None:
        payload = {**admin.get("/api/settings").json(), "sync_interval_minutes": 0}
        payload.pop("interval_warning", None)
        assert admin.put("/api/settings", json=payload).status_code == 422

    def test_corrupt_stored_settings_fall_back_to_defaults(
        self, db: DatabaseManager
    ) -> None:
        """A bad row must not stop the scheduler from running at all."""
        from app.database.models import AppSetting

        with db.session() as session:
            session.add(AppSetting(key="sync.sync_interval_minutes", value="not-a-number"))

        with db.session() as session:
            assert SettingsService().load(session).sync_interval_minutes == 5

    def test_settings_require_authentication(self, client: TestClient) -> None:
        assert client.get("/api/settings").status_code == 401
        assert client.put("/api/settings", json={}).status_code == 401
