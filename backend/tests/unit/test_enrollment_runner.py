"""Registering employees across both terminals.

The properties that matter, in order: nothing is written unless asked, an existing user is
never disturbed, one terminal failing does not stop the other, and a terminal that says
"done" is not believed until it is read back.
"""

from __future__ import annotations

import pytest

from app.biometric.base import DeviceUserRecord
from app.core.errors import DeviceUnavailableError
from app.database.models import Device
from app.hrms.client import HrmsEmployee
from app.services.enrollment_service import EnrollmentRunner
from tests.fakes.fake_device import FakeEnrollableDevice


def emp(code: str, name: str = "Some One", card: str | None = None) -> HrmsEmployee:
    return HrmsEmployee(employee_code=code, name=name, card_number=card)


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


def _runner(fakes: dict[str, FakeEnrollableDevice]) -> EnrollmentRunner:
    return EnrollmentRunner(device_factory=lambda record: fakes[record.name])


def _reconcile(db, runner, employees, *, dry_run):
    with db.session() as session:
        return runner.reconcile_devices(session, employees, dry_run=dry_run)


def test_dry_run_reports_who_would_be_added_and_writes_nothing(db, two_devices) -> None:
    fakes = {"Entry": FakeEnrollableDevice(), "Exit": FakeEnrollableDevice()}

    outcome = _reconcile(db, _runner(fakes), [emp("IQ-1004", "Manoj")], dry_run=True)

    assert outcome.dry_run is True
    assert [u.employee_code for u in outcome.devices[0].added] == ["IQ-1004"]
    assert fakes["Entry"].added == []
    assert fakes["Exit"].added == []
    assert fakes["Entry"].records == []


def test_the_missing_user_is_added_to_the_terminal_that_lacks_them(db, two_devices) -> None:
    """The Manoj case: present on Exit, absent from Entry."""
    fakes = {
        "Entry": FakeEnrollableDevice(),
        "Exit": FakeEnrollableDevice(records=[DeviceUserRecord(1, "IQ-1004", "Manoj")]),
    }

    outcome = _reconcile(db, _runner(fakes), [emp("IQ-1004", "Manoj S")], dry_run=False)

    entry, exit_ = outcome.devices
    assert entry.status == "OK"
    assert [u.employee_code for u in entry.added] == ["IQ-1004"]
    assert exit_.status == "OK"
    assert exit_.added == []
    assert exit_.already_present == 1
    assert [r.user_id for r in fakes["Entry"].records] == ["IQ-1004"]
    assert fakes["Exit"].added == []


def test_an_existing_user_is_never_overwritten(db, two_devices) -> None:
    """The fake overwrites an occupied slot exactly as set_user does, so this fails if a
    slot is ever reused."""
    existing = [
        DeviceUserRecord(1, "KEEP-1", "Keep One"),
        DeviceUserRecord(3, "KEEP-2", "Keep Two"),
    ]
    fakes = {
        "Entry": FakeEnrollableDevice(records=list(existing)),
        "Exit": FakeEnrollableDevice(),
    }

    _reconcile(db, _runner(fakes), [emp("NEW-1"), emp("NEW-2")], dry_run=False)

    codes = {r.user_id for r in fakes["Entry"].records}
    assert {"KEEP-1", "KEEP-2", "NEW-1", "NEW-2"} == codes
    assert sorted(r.uid for r in fakes["Entry"].records) == [1, 2, 3, 4]


def test_one_unreachable_terminal_does_not_stop_the_other(db, two_devices) -> None:
    fakes = {
        "Entry": FakeEnrollableDevice(
            fail_with=DeviceUnavailableError("Entry is unreachable."), fail_times=99
        ),
        "Exit": FakeEnrollableDevice(),
    }

    outcome = _reconcile(db, _runner(fakes), [emp("IQ-1")], dry_run=False)

    entry, exit_ = outcome.devices
    assert entry.status == "FAILED"
    assert "unreachable" in (entry.error_message or "")
    assert exit_.status == "OK"
    assert [r.user_id for r in fakes["Exit"].records] == ["IQ-1"]


def test_a_refused_add_is_reported_and_the_rest_continue(db, two_devices) -> None:
    fakes = {
        "Entry": FakeEnrollableDevice(fail_add_for={"BAD"}),
        "Exit": FakeEnrollableDevice(),
    }

    outcome = _reconcile(db, _runner(fakes), [emp("BAD"), emp("GOOD")], dry_run=False)

    entry = outcome.devices[0]
    assert entry.status == "PARTIAL"
    assert [f.employee_code for f in entry.failed] == ["BAD"]
    assert [a.employee_code for a in entry.added] == ["GOOD"]


def test_a_terminal_that_accepts_but_does_not_store_is_not_believed(db, two_devices) -> None:
    fakes = {
        "Entry": FakeEnrollableDevice(silently_drop={"GHOST"}),
        "Exit": FakeEnrollableDevice(),
    }

    outcome = _reconcile(db, _runner(fakes), [emp("GHOST"), emp("REAL")], dry_run=False)

    entry = outcome.devices[0]
    assert entry.status == "PARTIAL"
    assert [a.employee_code for a in entry.added] == ["REAL"]
    assert [f.employee_code for f in entry.failed] == ["GHOST"]
    assert "read back" in entry.failed[0].reason


def test_running_twice_adds_nothing_the_second_time(db, two_devices) -> None:
    fakes = {"Entry": FakeEnrollableDevice(), "Exit": FakeEnrollableDevice()}
    runner = _runner(fakes)

    _reconcile(db, runner, [emp("IQ-1"), emp("IQ-2")], dry_run=False)
    again = _reconcile(db, runner, [emp("IQ-1"), emp("IQ-2")], dry_run=False)

    assert all(d.added == [] and d.already_present == 2 for d in again.devices)


def test_a_disabled_terminal_is_left_alone(db, two_devices) -> None:
    with db.session() as session:
        session.query(Device).filter(Device.name == "Exit").update({"enabled": False})
    fakes = {"Entry": FakeEnrollableDevice(), "Exit": FakeEnrollableDevice()}

    outcome = _reconcile(db, _runner(fakes), [emp("IQ-1")], dry_run=False)

    assert [d.device_name for d in outcome.devices] == ["Entry"]
    assert fakes["Exit"].added == []


def test_the_terminal_is_always_disconnected(db, two_devices) -> None:
    fakes = {
        "Entry": FakeEnrollableDevice(fail_add_for={"X"}),
        "Exit": FakeEnrollableDevice(),
    }

    _reconcile(db, _runner(fakes), [emp("X")], dry_run=False)

    assert fakes["Entry"].connect_count == fakes["Entry"].disconnect_count == 1
    assert fakes["Exit"].connect_count == fakes["Exit"].disconnect_count == 1
