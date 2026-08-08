"""Locking, crash recovery and the suppression cache.

These cover the failures that only appear when the process dies at the wrong moment
— which, on a machine that reboots for Windows Update, is not a rare event.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums import SyncStatus, SyncTrigger
from app.core.errors import SyncInProgressError
from app.database.database import DatabaseManager
from app.database.models import Device, SyncRun, SyncState
from app.services.push_cache import PushedRecordCache
from app.services.sync_lock import (
    clear_locks_owned_by_this_process,
    device_lock,
    process_owner,
)
from app.services.sync_runner import close_interrupted_runs
from app.utils.datetime_utils import utcnow

STALE_AFTER = timedelta(minutes=30)


@pytest.fixture
def device_id(db: DatabaseManager) -> int:
    with db.session() as session:
        device = Device(name="Entry", ip_address="172.20.200.26", port=4370)
        session.add(device)
        session.flush()
        session.add(SyncState(device_id=device.id))
        return device.id


class TestDeviceLock:
    def test_lock_is_held_and_released(self, db: DatabaseManager, device_id: int) -> None:
        with db.session() as session, device_lock(session, device_id, stale_after=STALE_AFTER):
            pass
        with db.session() as session:
            assert session.query(SyncState).one().locked_at is None

    def test_second_acquisition_is_refused(self, db: DatabaseManager, device_id: int) -> None:
        """The scheduler and an operator's Sync Now must not run the same device
        concurrently — they would push the same window twice."""
        with (
            db.session() as outer,
            device_lock(outer, device_id, stale_after=STALE_AFTER),
            db.session() as inner,
            pytest.raises(SyncInProgressError),
            device_lock(inner, device_id, stale_after=STALE_AFTER),
        ):
            pass

    def test_different_devices_do_not_block_each_other(self, db: DatabaseManager) -> None:
        """RULE 4 — devices sync independently."""
        with db.session() as session:
            for name, ip in (("A", "10.0.0.1"), ("B", "10.0.0.2")):
                device = Device(name=name, ip_address=ip, port=4370)
                session.add(device)
                session.flush()
                session.add(SyncState(device_id=device.id))
            ids = [d.id for d in session.query(Device).all()]

        with (
            db.session() as s1,
            device_lock(s1, ids[0], stale_after=STALE_AFTER),
            db.session() as s2,
            device_lock(s2, ids[1], stale_after=STALE_AFTER),
        ):
            pass

    def test_lock_is_released_even_when_the_body_raises(
        self, db: DatabaseManager, device_id: int
    ) -> None:
        with (
            pytest.raises(RuntimeError),
            db.session() as session,
            device_lock(session, device_id, stale_after=STALE_AFTER),
        ):
            raise RuntimeError("boom")
        with db.session() as session:
            assert session.query(SyncState).one().locked_at is None

    def test_stale_lock_is_reclaimed(self, db: DatabaseManager, device_id: int) -> None:
        """Without this, a Windows restart mid-sync would wedge the device
        permanently and need someone to edit SQLite by hand."""
        with db.session() as session:
            state = session.query(SyncState).one()
            state.locked_at = utcnow() - timedelta(hours=2)
            state.lock_owner = "pid:99999"

        with db.session() as session, device_lock(session, device_id, stale_after=STALE_AFTER):
            pass

    def test_a_fresh_lock_from_another_process_is_respected(
        self, db: DatabaseManager, device_id: int
    ) -> None:
        with db.session() as session:
            state = session.query(SyncState).one()
            state.locked_at = utcnow()
            state.lock_owner = "pid:99999"

        with (
            db.session() as session,
            pytest.raises(SyncInProgressError),
            device_lock(session, device_id, stale_after=STALE_AFTER),
        ):
            pass

    def test_lock_is_visible_to_other_sessions_while_held(
        self, db: DatabaseManager, device_id: int
    ) -> None:
        """It is committed immediately; holding it in an open transaction would make
        it invisible until the sync finished, defeating the point."""
        with (
            db.session() as session,
            device_lock(session, device_id, stale_after=STALE_AFTER),
            db.session() as observer,
        ):
            assert observer.query(SyncState).one().locked_at is not None


class TestStartupRecovery:
    def test_locks_from_this_process_are_cleared(
        self, db: DatabaseManager, device_id: int
    ) -> None:
        with db.session() as session:
            state = session.query(SyncState).one()
            state.locked_at = utcnow()
            state.lock_owner = process_owner()

        with db.session() as session:
            assert clear_locks_owned_by_this_process(session) == 1
        with db.session() as session:
            assert session.query(SyncState).one().locked_at is None

    def test_locks_from_another_process_are_left_alone(
        self, db: DatabaseManager, device_id: int
    ) -> None:
        """Unsafe to clear blindly if someone ever ran two bridges on one database."""
        with db.session() as session:
            state = session.query(SyncState).one()
            state.locked_at = utcnow()
            state.lock_owner = "pid:99999"

        with db.session() as session:
            assert clear_locks_owned_by_this_process(session) == 0

    def test_interrupted_runs_are_closed(self, db: DatabaseManager, device_id: int) -> None:
        """A RUNNING row from a dead process would otherwise make the dashboard
        claim a sync is in progress forever."""
        with db.session() as session:
            session.add(
                SyncRun(
                    device_id=device_id,
                    trigger=SyncTrigger.SCHEDULED,
                    status=SyncStatus.RUNNING,
                )
            )

        with db.session() as session:
            assert close_interrupted_runs(session) == 1

        with db.session() as session:
            run = session.query(SyncRun).one()
            assert run.status is SyncStatus.INTERRUPTED
            assert run.completed_at is not None

    def test_recovery_does_not_touch_the_watermark(
        self, db: DatabaseManager, device_id: int
    ) -> None:
        """Whether HRMS accepted those punches is unknown; assume it did not."""
        marker = utcnow()
        with db.session() as session:
            session.query(SyncState).one().last_successful_push_at = marker
            session.add(
                SyncRun(
                    device_id=device_id,
                    trigger=SyncTrigger.SCHEDULED,
                    status=SyncStatus.RUNNING,
                )
            )

        with db.session() as session:
            close_interrupted_runs(session)
        with db.session() as session:
            assert session.query(SyncState).one().last_successful_push_at == marker

    def test_completed_runs_are_untouched(self, db: DatabaseManager, device_id: int) -> None:
        with db.session() as session:
            session.add(
                SyncRun(
                    device_id=device_id,
                    trigger=SyncTrigger.SCHEDULED,
                    status=SyncStatus.SUCCESS,
                    completed_at=utcnow(),
                )
            )
        with db.session() as session:
            assert close_interrupted_runs(session) == 0


class TestPushedRecordCache:
    def test_remembers_what_was_pushed(self) -> None:
        cache = PushedRecordCache()
        cache.add_many(1, ["a", "b"])
        assert cache.contains(1, "a") is True
        assert cache.contains(1, "c") is False

    def test_devices_are_isolated(self) -> None:
        cache = PushedRecordCache()
        cache.add_many(1, ["a"])
        assert cache.contains(2, "a") is False

    def test_evicts_oldest_beyond_capacity(self) -> None:
        """Bounded so a busy terminal cannot grow it without limit."""
        cache = PushedRecordCache(capacity_per_device=3)
        cache.add_many(1, ["a", "b", "c", "d"])
        assert cache.contains(1, "a") is False
        assert cache.contains(1, "d") is True
        assert cache.size(1) == 3

    def test_recent_use_protects_an_entry_from_eviction(self) -> None:
        cache = PushedRecordCache(capacity_per_device=3)
        cache.add_many(1, ["a", "b", "c"])
        cache.contains(1, "a")  # touch
        cache.add_many(1, ["d"])
        assert cache.contains(1, "a") is True
        assert cache.contains(1, "b") is False

    def test_filter_unsent(self) -> None:
        cache = PushedRecordCache()
        cache.add_many(1, ["a"])
        assert cache.filter_unsent(1, ["a", "b"]) == ["b"]

    def test_forgetting_a_device_clears_only_that_device(self) -> None:
        cache = PushedRecordCache()
        cache.add_many(1, ["a"])
        cache.add_many(2, ["b"])
        cache.forget_device(1)
        assert cache.contains(1, "a") is False
        assert cache.contains(2, "b") is True
