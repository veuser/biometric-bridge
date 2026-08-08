"""Model behaviour that the rest of the system relies on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import StatementError

from app.core.enums import SyncStatus, SyncTrigger
from app.database.database import DatabaseManager
from app.database.models import Device, SyncRun, SyncState

IST = ZoneInfo("Asia/Kolkata")


def _device(**overrides: object) -> Device:
    defaults: dict[str, object] = {
        "name": "Entry",
        "ip_address": "172.20.200.26",
        "port": 4370,
    }
    defaults.update(overrides)
    return Device(**defaults)  # type: ignore[arg-type]


def test_utc_datetime_rejects_naive_values(db: DatabaseManager) -> None:
    """The schema is the enforcement point for RULE 15.

    A naive datetime cannot be persisted, so the "never mix naive and aware" rule
    cannot be broken by an oversight in a service.
    """
    with pytest.raises(StatementError, match="naive datetime"), db.session() as session:
        device = _device()
        session.add(device)
        session.flush()
        session.add(
            SyncState(device_id=device.id, last_successful_push_at=datetime(2026, 8, 8, 12, 0))
        )
        session.flush()


def test_aware_non_utc_values_are_normalised_to_utc(db: DatabaseManager) -> None:
    with db.session() as session:
        device = _device()
        session.add(device)
        session.flush()
        session.add(
            SyncState(
                device_id=device.id,
                last_successful_push_at=datetime(2026, 8, 8, 12, 7, 9, tzinfo=IST),
            )
        )

    with db.session() as session:
        state = session.query(SyncState).one()
        assert state.last_successful_push_at == datetime(2026, 8, 8, 6, 37, 9, tzinfo=UTC)
        assert state.last_successful_push_at.tzinfo is UTC


def test_timestamps_come_back_aware(db: DatabaseManager) -> None:
    """SQLite stores no offset, so a naive value on read would break comparisons."""
    with db.session() as session:
        session.add(_device())

    with db.session() as session:
        device = session.query(Device).one()
        assert device.created_at.tzinfo is not None


def test_device_endpoint_is_unique(db: DatabaseManager) -> None:
    """Two device rows pointing at the same box would double-push every punch."""
    from sqlalchemy.exc import IntegrityError

    with db.session() as session:
        session.add(_device(name="Entry"))

    with pytest.raises(IntegrityError), db.session() as session:
        session.add(_device(name="Entry duplicate"))


def test_deleting_a_device_removes_its_sync_state(db: DatabaseManager) -> None:
    with db.session() as session:
        device = _device()
        session.add(device)
        session.flush()
        session.add(SyncState(device_id=device.id))

    with db.session() as session:
        session.delete(session.query(Device).one())

    with db.session() as session:
        assert session.query(SyncState).count() == 0


def test_sync_run_duration(db: DatabaseManager) -> None:
    started = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    run = SyncRun(
        device_id=1,
        trigger=SyncTrigger.SCHEDULED,
        started_at=started,
        completed_at=started + timedelta(milliseconds=1500),
        status=SyncStatus.SUCCESS,
    )
    assert run.duration_ms == 1500


def test_sync_run_duration_is_none_while_running() -> None:
    run = SyncRun(
        device_id=1,
        trigger=SyncTrigger.SCHEDULED,
        started_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        status=SyncStatus.RUNNING,
    )
    assert run.duration_ms is None
