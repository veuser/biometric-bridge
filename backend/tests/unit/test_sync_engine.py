"""The synchronization engine.

The priority suite for this project. Every assertion here corresponds to a way
attendance data can be silently lost or duplicated, so each test names the failure it
prevents rather than just the behaviour it observes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.enums import DeviceRole, SyncStatus, SyncTrigger
from app.core.errors import HrmsUnavailableError
from app.database.database import DatabaseManager
from app.database.models import Device, SyncRun, SyncState
from app.hrms.client import HrmsClient
from app.services.push_cache import PushedRecordCache
from app.services.settings_service import SyncSettings
from app.services.sync_service import SyncService, _safe_watermark
from app.utils.retry import RetryPolicy
from tests.fakes.fake_device import FakeDevice
from tests.fakes.fake_hrms import FakeHrms
from tests.unit.test_hrms_mapper import configured, make_punch

IST = ZoneInfo("Asia/Kolkata")
API_KEY = "hrms-live-key-abc123456"

#: 12:20 IST on the device == 06:50 UTC.
DEVICE_NOW = datetime(2026, 8, 8, 12, 20, 0)


def ist(hour: int, minute: int, second: int = 0) -> datetime:
    """A device-local naive timestamp."""
    return datetime(2026, 8, 8, hour, minute, second)


def utc_of(hour: int, minute: int, second: int = 0) -> datetime:
    """The UTC equivalent of the same IST wall-clock time."""
    return datetime(2026, 8, 8, hour, minute, second, tzinfo=IST).astimezone(UTC)


@pytest.fixture
def sync_settings() -> SyncSettings:
    """Named to avoid shadowing conftest's app-level `settings` fixture, which the
    `db` fixture depends on."""
    return SyncSettings(sync_overlap_seconds=60, initial_lookback_hours=24)


@pytest.fixture
def device_row(db: DatabaseManager) -> Device:
    with db.session() as session:
        device = Device(
            name="Exit",
            ip_address="172.20.200.25",
            port=4370,
            role=DeviceRole.CHECK_OUT,
            serial_number="BJ2C184562186",
        )
        session.add(device)
        session.flush()
        session.add(SyncState(device_id=device.id))
        session.expunge_all()
        return device


class Harness:
    """Runs the engine against a fake device and a fake HRMS."""

    def __init__(self, db: DatabaseManager, settings: SyncSettings) -> None:
        self.db = db
        self.settings = settings
        self.device = FakeDevice(device_time=DEVICE_NOW, serial_number="BJ2C184562186")
        self.hrms = FakeHrms()
        self.cache = PushedRecordCache()
        self.service = SyncService(
            push_cache=self.cache,
            # No sleeping in tests; retry timing is covered in test_retry.py.
            retry_policy=RetryPolicy(delays_seconds=(0.0, 0.0), jitter_fraction=0.0),
        )

    def client(self, **profile_overrides: object) -> HrmsClient:
        return HrmsClient(
            base_url="https://hrms.example.com",
            profile=configured(**profile_overrides),
            transport=self.hrms.transport(),
        )

    def run(
        self,
        trigger: SyncTrigger = SyncTrigger.SCHEDULED,
        *,
        with_hrms: bool = True,
        manual_from: datetime | None = None,
        manual_to: datetime | None = None,
        **profile_overrides: object,
    ) -> tuple[object, SyncState]:
        with self.db.session() as session:
            device_row = session.query(Device).one()
            outcome = self.service.run_sync(
                session,
                device_record=device_row,
                device=self.device,
                hrms_client=self.client(**profile_overrides) if with_hrms else None,
                api_key=API_KEY if with_hrms else None,
                tenant=None,
                settings=self.settings,
                trigger=trigger,
                manual_from=manual_from,
                manual_to=manual_to,
            )
        with self.db.session() as session:
            state = session.query(SyncState).one()
            session.expunge_all()
        return outcome, state


@pytest.fixture
def harness(db: DatabaseManager, sync_settings: SyncSettings, device_row: Device) -> Harness:
    return Harness(db, sync_settings)


class TestFirstSync:
    def test_first_run_uses_the_initial_lookback(self, harness: Harness) -> None:
        """Bounds the first sync so a device holding two years of history does not
        push its whole buffer into HRMS on install."""
        harness.device.add_punch("IQ-1004", ist(12, 7, 9), status=1, punch=1)
        harness.device.add_punch("ancient", datetime(2024, 1, 1, 9, 0), status=1, punch=1)

        outcome, state = harness.run()
        assert outcome.status is SyncStatus.SUCCESS
        assert outcome.records_success == 1, "the two-year-old record must be outside the window"

    def test_watermark_starts_null_and_is_set_from_the_data(self, harness: Harness) -> None:
        harness.device.add_punch("IQ-1004", ist(12, 7, 9))
        _, state = harness.run()
        assert state.last_successful_push_at == utc_of(12, 7, 9)


class TestWatermarkRules:
    def test_watermark_is_the_max_accepted_punch_not_the_window_end(
        self, harness: Harness
    ) -> None:
        """Advancing to the window end would skip any punch that lands between the
        last record and the read."""
        harness.device.add_punch("a", ist(12, 8))
        harness.device.add_punch("b", ist(12, 14))
        _, state = harness.run()
        assert state.last_successful_push_at == utc_of(12, 14)

    def test_watermark_does_not_move_when_hrms_fails(self, harness: Harness) -> None:
        """RULE 3. This is the single most important assertion in the suite: if the
        watermark advanced past unpushed records, they would never be read again."""
        harness.device.add_punch("a", ist(12, 8))
        harness.hrms.status_code = 500

        outcome, state = harness.run()
        assert outcome.status is SyncStatus.HRMS_UNAVAILABLE
        assert state.last_successful_push_at is None

    def test_failed_window_is_retried_in_full_next_cycle(self, harness: Harness) -> None:
        harness.device.add_punch("a", ist(12, 8))
        harness.hrms.status_code = 503
        harness.run()

        harness.hrms.status_code = 200
        outcome, state = harness.run()
        assert outcome.records_success == 1
        assert state.last_successful_push_at == utc_of(12, 8)

    def test_idle_device_does_not_advance_the_watermark(self, harness: Harness) -> None:
        outcome, state = harness.run()
        assert outcome.status is SyncStatus.NO_RECORDS
        assert state.last_successful_push_at is None

    def test_watermark_never_moves_backwards(self, harness: Harness) -> None:
        """An out-of-order or catch-up run must not rewind automatic sync."""
        harness.device.add_punch("late", ist(12, 14))
        _, state = harness.run()
        assert state.last_successful_push_at == utc_of(12, 14)

        harness.cache.clear()
        harness.device.buffer.clear()
        harness.device.add_punch("early", ist(12, 9))
        _, state = harness.run()
        assert state.last_successful_push_at == utc_of(12, 14)


class TestIncrementalWindow:
    def test_second_run_starts_from_the_watermark_minus_overlap(
        self, harness: Harness
    ) -> None:
        harness.device.add_punch("a", ist(12, 14))
        harness.run()

        with harness.db.session() as session:
            run = session.query(SyncRun).order_by(SyncRun.id).first()
            assert run is not None

        harness.device.advance_clock(timedelta(minutes=5))
        harness.run()

        with harness.db.session() as session:
            latest = session.query(SyncRun).order_by(SyncRun.id.desc()).first()
            assert latest is not None
            # 12:14 minus the 60s overlap.
            assert latest.from_time == utc_of(12, 13)

    def test_overlap_boundary_record_is_re_read_but_not_re_pushed(
        self, harness: Harness
    ) -> None:
        """Without suppression an idle device re-pushes its last punch on every
        cycle — roughly 150 times overnight at a 5-minute interval."""
        harness.device.add_punch("a", ist(12, 14))
        harness.run()
        assert harness.hrms.call_count == 1

        harness.device.advance_clock(timedelta(minutes=5))
        outcome, _ = harness.run()

        assert harness.hrms.call_count == 1, "the boundary record must not be re-sent"
        assert outcome.records_suppressed == 1
        assert outcome.status is SyncStatus.NO_RECORDS

    def test_suppression_only_applies_to_accepted_records(self, harness: Harness) -> None:
        harness.device.add_punch("a", ist(12, 8))
        harness.hrms.status_code = 500
        harness.run()

        harness.hrms.status_code = 200
        outcome, _ = harness.run()
        assert outcome.records_success == 1, "a rejected record must not be suppressed"


class TestClockGuards:
    def test_backwards_clock_refuses_instead_of_inverting_the_window(
        self, harness: Harness
    ) -> None:
        """The failure this prevents: an inverted window silently returns nothing
        for hours while the dashboard stays green."""
        harness.device.add_punch("a", ist(12, 14))
        harness.run()

        harness.device.advance_clock(timedelta(hours=-2))
        outcome, state = harness.run()

        assert outcome.status is SyncStatus.DEVICE_CLOCK_REGRESSION
        assert state.last_successful_push_at == utc_of(12, 14), "watermark must survive"

    def test_forward_clock_jump_refuses_instead_of_skipping_records(
        self, harness: Harness
    ) -> None:
        harness.device.add_punch("a", ist(12, 14))
        harness.run()

        harness.device.advance_clock(timedelta(hours=3))
        outcome, state = harness.run()

        assert outcome.status is SyncStatus.DEVICE_CLOCK_JUMP
        assert state.last_successful_push_at == utc_of(12, 14)

    def test_clock_errors_are_not_retryable(self) -> None:
        """Retrying a bad clock reads the same bad clock."""
        from app.core.errors import DeviceClockJumpError, DeviceClockRegressionError

        assert DeviceClockRegressionError("x").retryable is False
        assert DeviceClockJumpError("x").retryable is False

    def test_small_drift_is_recorded_but_does_not_block(self, harness: Harness) -> None:
        """The reference terminals run ~150s slow; that must not stop sync."""
        harness.device.advance_clock(timedelta(seconds=-150))
        harness.device.add_punch("a", ist(12, 10))
        outcome, state = harness.run()
        assert outcome.status is SyncStatus.SUCCESS
        assert state.clock_offset_seconds is not None


class TestManualRange:
    def test_manual_sync_never_touches_the_watermark(self, harness: Harness) -> None:
        """RULE 5. A historical recovery run must not disturb automatic sync."""
        harness.device.add_punch("a", ist(12, 14))
        harness.run()
        with harness.db.session() as session:
            baseline = session.query(SyncState).one().last_successful_push_at

        harness.cache.clear()
        harness.device.add_punch("old", ist(9, 0))
        outcome, state = harness.run(
            SyncTrigger.MANUAL_RANGE,
            manual_from=utc_of(8, 0),
            manual_to=utc_of(10, 0),
        )

        assert outcome.status is SyncStatus.SUCCESS
        assert outcome.records_success == 1
        assert state.last_successful_push_at == baseline

    def test_manual_sync_still_pushes_and_is_recorded(self, harness: Harness) -> None:
        harness.device.add_punch("old", ist(9, 0))
        outcome, _ = harness.run(
            SyncTrigger.MANUAL_RANGE, manual_from=utc_of(8, 0), manual_to=utc_of(10, 0)
        )
        assert outcome.records_success == 1
        with harness.db.session() as session:
            run = session.query(SyncRun).one()
            assert run.trigger == SyncTrigger.MANUAL_RANGE

    def test_manual_sync_ignores_the_clock_guard(self, harness: Harness) -> None:
        """Recovering from a clock problem is exactly when manual sync is needed."""
        harness.device.add_punch("a", ist(12, 14))
        harness.run()
        harness.device.advance_clock(timedelta(hours=-5))
        harness.cache.clear()

        outcome, _ = harness.run(
            SyncTrigger.MANUAL_RANGE, manual_from=utc_of(12, 0), manual_to=utc_of(13, 0)
        )
        assert outcome.status is SyncStatus.SUCCESS


class TestRecordHandling:
    def test_invalid_records_are_counted_not_fatal(self, harness: Harness) -> None:
        harness.device.add_punch("good", ist(12, 8))
        harness.device.add_punch("", ist(12, 9))
        outcome, _ = harness.run()
        assert outcome.records_success == 1
        assert outcome.records_invalid == 1

    def test_all_invalid_window_is_distinct_from_idle(self, harness: Harness) -> None:
        """Otherwise a persistently misbehaving terminal looks like a quiet one."""
        harness.device.add_punch("", ist(12, 8))
        outcome, state = harness.run()
        assert outcome.status is SyncStatus.INVALID_RECORD
        assert state.last_successful_push_at is None

    def test_batching_splits_large_windows(self, harness: Harness) -> None:
        for minute in range(10):
            harness.device.add_punch(f"IQ-{minute}", ist(12, minute))
        outcome, _ = harness.run(batch={"size": 3})
        assert outcome.records_success == 10
        assert harness.hrms.call_count == 4

    def test_buffer_and_window_counts_are_tracked_separately(
        self, harness: Harness
    ) -> None:
        """buffer_records is the real cost driver and must not be conflated."""
        harness.device.add_punch("in", ist(12, 8))
        harness.device.add_punch("out", datetime(2024, 1, 1, 9, 0))
        outcome, _ = harness.run()
        assert outcome.buffer_records == 2
        assert outcome.window_records == 1


class TestPartialAcceptance:
    def _partial_profile(self) -> dict[str, object]:
        return {
            "field_map": {"client_record_id": "clientRecordId"},
            "response": {
                "results_path": "results",
                "result_id_path": "id",
                "result_ok_path": "accepted",
            },
        }

    def test_watermark_advances_when_rejections_are_all_later(
        self, harness: Harness
    ) -> None:
        """Safe: nothing unpushed is left behind the watermark."""
        harness.device.add_punch("early", ist(12, 5))
        harness.device.add_punch("late", ist(12, 15))

        with harness.db.session() as session:
            device_row = session.query(Device).one()
            from app.biometric.normalizer import make_client_record_id

            early_id = make_client_record_id(
                device_serial=device_row.serial_number,
                employee_code="early",
                punch_time=utc_of(12, 5),
                raw_punch=0,
            )
            late_id = make_client_record_id(
                device_serial=device_row.serial_number,
                employee_code="late",
                punch_time=utc_of(12, 15),
                raw_punch=0,
            )

        harness.hrms.response_body = {
            "results": [
                {"id": early_id, "accepted": True},
                {"id": late_id, "accepted": False},
            ]
        }
        outcome, state = harness.run(**self._partial_profile())

        assert outcome.status is SyncStatus.PARTIAL
        assert state.last_successful_push_at == utc_of(12, 5)

    def test_watermark_frozen_when_rejections_interleave(self, harness: Harness) -> None:
        """Advancing would strand the earlier rejected record behind the watermark
        forever — it would never be read again."""
        accepted = [make_punch(client_record_id="a", punch_time=utc_of(12, 15))]
        rejected = [make_punch(client_record_id="b", punch_time=utc_of(12, 5))]
        assert _safe_watermark(accepted, rejected) is None

    def test_watermark_is_max_accepted_when_nothing_rejected(self) -> None:
        accepted = [
            make_punch(client_record_id="a", punch_time=utc_of(12, 5)),
            make_punch(client_record_id="b", punch_time=utc_of(12, 15)),
        ]
        assert _safe_watermark(accepted, []) == utc_of(12, 15)

    def test_no_watermark_when_nothing_was_accepted(self) -> None:
        rejected = [make_punch(client_record_id="b", punch_time=utc_of(12, 5))]
        assert _safe_watermark([], rejected) is None


class TestFailureModes:
    def test_device_unavailable_closes_the_run_and_keeps_the_watermark(
        self, harness: Harness
    ) -> None:
        harness.device.add_punch("a", ist(12, 8))
        harness.run()

        harness.device.fail_with = HrmsUnavailableError("terminal down")
        harness.device.fail_times = 99
        outcome, state = harness.run()

        assert outcome.status is not SyncStatus.RUNNING, "a run must never be left open"
        assert state.last_successful_push_at == utc_of(12, 8)

    def test_hrms_auth_failure_is_reported_distinctly(self, harness: Harness) -> None:
        harness.device.add_punch("a", ist(12, 8))
        harness.hrms.status_code = 401
        outcome, _ = harness.run()
        assert outcome.status is SyncStatus.HRMS_AUTHENTICATION_FAILED

    def test_transient_hrms_failure_is_retried_within_the_run(
        self, harness: Harness
    ) -> None:
        harness.device.add_punch("a", ist(12, 8))
        harness.hrms.fail_times = 2
        outcome, state = harness.run()
        assert outcome.status is SyncStatus.SUCCESS
        assert state.last_successful_push_at == utc_of(12, 8)

    def test_missing_hrms_config_does_not_advance_the_watermark(
        self, harness: Harness
    ) -> None:
        harness.device.add_punch("a", ist(12, 8))
        outcome, state = harness.run(with_hrms=False)
        assert state.last_successful_push_at is None
        assert outcome.status is not SyncStatus.SUCCESS

    def test_every_run_reaches_a_terminal_status(self, harness: Harness) -> None:
        """A run stuck in RUNNING blocks the device on the next cycle."""
        harness.device.add_punch("a", ist(12, 8))
        harness.hrms.status_code = 500
        harness.run()
        with harness.db.session() as session:
            for run in session.query(SyncRun).all():
                assert run.status is not SyncStatus.RUNNING
                assert run.completed_at is not None
