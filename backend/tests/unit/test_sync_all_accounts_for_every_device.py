"""Every enabled device must appear in a "sync all" result.

The failure this guards against is the one the original proof-of-concept had: a device
that could not be read contributed nothing to the returned list, so the result looked
complete and a terminal was quietly not being collected. Silence is the worst outcome
for attendance, because nobody goes looking for punches they do not know are missing.
"""

from __future__ import annotations

import pytest

from app.core.enums import SyncStatus, SyncTrigger
from app.core.errors import DeviceConfigurationError, SyncInProgressError
from app.database.models import Device
from app.services.sync_runner import SyncRunner


@pytest.fixture
def two_devices(db):
    with db.session() as session:
        for i, name in enumerate(("Entry", "Exit"), start=1):
            session.add(
                Device(
                    name=name,
                    ip_address=f"10.0.0.{i}",
                    port=4370,
                    role="CHECK_IN" if i == 1 else "CHECK_OUT",
                    enabled=True,
                )
            )
    with db.session() as session:
        return [d.id for d in session.query(Device).order_by(Device.id).all()]


def _runner_where_first_device_raises(exc: Exception) -> SyncRunner:
    runner = SyncRunner()
    calls: list[int] = []

    def fake_run_for_device(db, device_record, trigger, **kwargs):
        calls.append(device_record.id)
        if len(calls) == 1:
            raise exc
        from app.services.sync_service import SyncOutcome

        return SyncOutcome(run_id=99, device_id=device_record.id, status=SyncStatus.NO_RECORDS)

    runner.run_for_device = fake_run_for_device  # type: ignore[method-assign]
    return runner


class TestEveryDeviceIsAccountedFor:
    def test_a_locked_device_is_reported_not_dropped(self, db, two_devices) -> None:
        """The reachable case: the scheduler holds the lock and the operator clicks Sync."""
        runner = _runner_where_first_device_raises(
            SyncInProgressError("A synchronization is already running for this device.")
        )

        with db.session() as session:
            outcomes = runner.run_all_enabled(session, SyncTrigger.CATCH_UP)

        assert len(outcomes) == 2, "a skipped device must still produce a row"

        skipped = outcomes[0]
        assert skipped.device_id == two_devices[0]
        assert skipped.status is SyncStatus.SKIPPED
        assert "already running" in (skipped.error_message or "")

    def test_the_other_devices_still_run(self, db, two_devices) -> None:
        """One bad terminal must never halt collection site-wide."""
        runner = _runner_where_first_device_raises(DeviceConfigurationError("bad config"))

        with db.session() as session:
            outcomes = runner.run_all_enabled(session, SyncTrigger.CATCH_UP)

        assert outcomes[1].device_id == two_devices[1]
        assert outcomes[1].status is SyncStatus.NO_RECORDS

    def test_an_unexpected_error_does_not_leak_its_message(self, db, two_devices) -> None:
        """Arbitrary exception text can carry paths or connection strings, and this
        string is rendered in the portal."""
        runner = _runner_where_first_device_raises(
            RuntimeError("postgres://user:hunter2@10.0.0.9/db exploded")
        )

        with db.session() as session:
            outcomes = runner.run_all_enabled(session, SyncTrigger.CATCH_UP)

        message = outcomes[0].error_message or ""
        assert outcomes[0].status is SyncStatus.SKIPPED
        assert "hunter2" not in message
        assert "RuntimeError" in message

    def test_skipped_produces_no_run_row(self, db, two_devices) -> None:
        """Sync History records executions. Nothing executed, so nothing belongs there."""
        from app.database.models import SyncRun

        runner = _runner_where_first_device_raises(SyncInProgressError("busy"))

        with db.session() as session:
            outcomes = runner.run_all_enabled(session, SyncTrigger.CATCH_UP)

        assert outcomes[0].run_id == 0
        with db.session() as session:
            assert session.query(SyncRun).count() == 0
