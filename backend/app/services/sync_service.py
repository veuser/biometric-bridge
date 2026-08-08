"""The synchronization engine.

Every rule that can lose or duplicate attendance data lives here, so they are stated
once, in one place:

**RULE 3 — the watermark advances only after HRMS accepts.** It is set to the maximum
punch time actually accepted, never to the end of the window. If the device is idle
the watermark does not move; if any batch fails the whole window is retried next
cycle.

**RULE 4 — every device has its own watermark.** There is no global cursor.

**RULE 5 — a manual range sync never touches the watermark.** It is a historical
recovery operation, so it must not disturb automatic sync.

**Clock guards.** The window is built from the *device* clock, not the bridge clock,
and refuses to run at all if the device clock has moved backwards past the watermark
or jumped further than tolerated. Both produce silent, invisible data loss otherwise
— an inverted window returns nothing for hours behind a green dashboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.biometric.base import BiometricDevice
from app.biometric.normalizer import BiometricPunch, normalize_many
from app.core.enums import DeviceRole, SyncStatus, SyncTrigger
from app.core.errors import (
    BridgeError,
    DeviceClockJumpError,
    DeviceClockRegressionError,
    HrmsNotConfiguredError,
)
from app.core.logging import get_logger
from app.database.models import Device, SyncRun, SyncState
from app.hrms.client import HrmsClient, PushResult
from app.hrms.mapper import iter_batches
from app.services.push_cache import PushedRecordCache
from app.services.settings_service import SyncSettings
from app.utils.datetime_utils import device_naive_to_utc, utc_to_device_naive, utcnow
from app.utils.retry import RetryPolicy, call_with_retry

logger = get_logger(__name__)


@dataclass(slots=True)
class SyncOutcome:
    run_id: int
    device_id: int
    status: SyncStatus
    buffer_records: int = 0
    window_records: int = 0
    records_sent: int = 0
    records_success: int = 0
    records_failed: int = 0
    records_invalid: int = 0
    records_suppressed: int = 0
    watermark_advanced_to: datetime | None = None
    error_message: str | None = None


@dataclass(slots=True)
class _Window:
    """A sync window, held in both clock bases at once.

    Device-naive values filter the records the terminal gives us; UTC values are what
    gets recorded and compared. Keeping both together is what stops the two from
    being mixed up somewhere downstream.
    """

    from_device: datetime
    to_device: datetime
    from_utc: datetime
    to_utc: datetime


class SyncService:
    def __init__(
        self,
        *,
        push_cache: PushedRecordCache | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._cache = push_cache or PushedRecordCache()
        self._retry = retry_policy or RetryPolicy()

    # ── public entry points ──────────────────────────────────────────────────

    def run_sync(
        self,
        db: DbSession,
        *,
        device_record: Device,
        device: BiometricDevice,
        hrms_client: HrmsClient | None,
        api_key: str | None,
        tenant: str | None,
        settings: SyncSettings,
        trigger: SyncTrigger,
        manual_from: datetime | None = None,
        manual_to: datetime | None = None,
    ) -> SyncOutcome:
        """Execute one sync. Assumes the caller already holds the device lock."""
        state = self._get_state(db, device_record.id)
        run = SyncRun(
            device_id=device_record.id, trigger=trigger, status=SyncStatus.RUNNING
        )
        db.add(run)
        db.flush()

        state.last_sync_started_at = run.started_at
        db.flush()

        try:
            outcome = self._execute(
                db,
                run=run,
                state=state,
                device_record=device_record,
                device=device,
                hrms_client=hrms_client,
                api_key=api_key,
                tenant=tenant,
                settings=settings,
                trigger=trigger,
                manual_from=manual_from,
                manual_to=manual_to,
            )
        except BridgeError as exc:
            return self._finish_failed(db, run, state, exc.status, exc.message)
        except Exception as exc:
            # Unexpected failures must still close the run, or the device stays
            # RUNNING forever and the next cycle refuses to start.
            logger.exception(
                "Unexpected error during sync.", extra={"device_id": device_record.id}
            )
            return self._finish_failed(
                db, run, state, SyncStatus.UNKNOWN_ERROR, f"Unexpected error: {type(exc).__name__}"
            )

        return outcome

    # ── the algorithm ────────────────────────────────────────────────────────

    def _execute(
        self,
        db: DbSession,
        *,
        run: SyncRun,
        state: SyncState,
        device_record: Device,
        device: BiometricDevice,
        hrms_client: HrmsClient | None,
        api_key: str | None,
        tenant: str | None,
        settings: SyncSettings,
        trigger: SyncTrigger,
        manual_from: datetime | None,
        manual_to: datetime | None,
    ) -> SyncOutcome:
        tz = ZoneInfo(device_record.timezone or settings.timezone)

        device_now = device.get_device_time()
        offset_seconds = (device_now - datetime.now()).total_seconds()  # noqa: DTZ005

        # Captured *before* the new value is stored: the jump guard compares this
        # run's offset against the previous run's, and overwriting first would make
        # it compare the value with itself and never fire.
        previous_offset = state.clock_offset_seconds

        if trigger is SyncTrigger.MANUAL_RANGE:
            assert manual_from is not None and manual_to is not None
            window = _Window(
                from_device=utc_to_device_naive(manual_from, tz),
                to_device=utc_to_device_naive(manual_to, tz),
                from_utc=manual_from,
                to_utc=manual_to,
            )
        else:
            self._guard_clock(
                state, device_now, tz, offset_seconds, previous_offset, settings
            )
            window = self._computed_window(state, device_now, tz, settings)

        # Recorded only after the clock guards have passed, so a refused run does
        # not leave a nonsensical window in the history.
        state.clock_offset_seconds = offset_seconds
        run.from_time = window.from_utc
        run.to_time = window.to_utc
        db.flush()

        read = device.get_attendance_logs(window.from_device, window.to_device)
        run.buffer_records = read.buffer_records
        run.window_records = read.window_records
        run.device_read_ms = read.read_duration_ms
        db.flush()

        normalized = normalize_many(
            read.punches,
            device_id=device_record.id,
            device_role=DeviceRole(device_record.role),
            device_serial=device_record.serial_number,
            tz=tz,
        )
        run.records_invalid = normalized.invalid_count

        if not normalized.punches:
            # An all-invalid window is reported distinctly from a genuinely idle
            # device, so a persistently misbehaving terminal is visible.
            status = (
                SyncStatus.INVALID_RECORD
                if normalized.invalid_count
                else SyncStatus.NO_RECORDS
            )
            return self._finish_ok(db, run, state, status, watermark=None)

        # Suppress anything already accepted this process lifetime, so the overlap
        # window is not re-pushed on every cycle.
        pending = [
            p
            for p in normalized.punches
            if not self._cache.contains(device_record.id, p.client_record_id)
        ]
        run.records_suppressed = len(normalized.punches) - len(pending)

        if not pending:
            return self._finish_ok(db, run, state, SyncStatus.NO_RECORDS, watermark=None)

        if hrms_client is None or not api_key:
            raise HrmsNotConfiguredError(
                "HRMS is not configured, so there is nowhere to send these punches."
            )

        return self._push(
            db,
            run=run,
            state=state,
            device_id=device_record.id,
            punches=pending,
            hrms_client=hrms_client,
            api_key=api_key,
            tenant=tenant,
            trigger=trigger,
            profile_batches=list(iter_batches(pending, hrms_client.profile)),
        )

    def _push(
        self,
        db: DbSession,
        *,
        run: SyncRun,
        state: SyncState,
        device_id: int,
        punches: list[BiometricPunch],
        hrms_client: HrmsClient,
        api_key: str,
        tenant: str | None,
        trigger: SyncTrigger,
        profile_batches: list[list[BiometricPunch]],
    ) -> SyncOutcome:
        started = time.monotonic()
        accepted: list[BiometricPunch] = []
        rejected: list[BiometricPunch] = []
        sent = 0

        def push_one(b: list[BiometricPunch]) -> PushResult:
            return hrms_client.push(b, api_key=api_key, tenant=tenant)

        for batch in profile_batches:
            result = call_with_retry(
                partial(push_one, batch),
                policy=self._retry,
                description=f"HRMS push for device {device_id}",
            )
            sent += len(batch)
            batch_accepted, batch_rejected = _split_batch(batch, result)
            accepted.extend(batch_accepted)
            rejected.extend(batch_rejected)

        run.hrms_ms = int((time.monotonic() - started) * 1000)
        run.records_sent = sent
        run.records_success = len(accepted)
        run.records_failed = len(rejected)

        # Only cache what HRMS actually took — a rejected record must be retried.
        self._cache.add_many(device_id, [p.client_record_id for p in accepted])

        watermark = None
        if trigger.advances_watermark:
            watermark = _safe_watermark(accepted, rejected)

        status = SyncStatus.PARTIAL if rejected else SyncStatus.SUCCESS
        return self._finish_ok(db, run, state, status, watermark=watermark)

    # ── window and clock ─────────────────────────────────────────────────────

    def _computed_window(
        self,
        state: SyncState,
        device_now: datetime,
        tz: ZoneInfo,
        settings: SyncSettings,
    ) -> _Window:
        to_device = device_now
        if state.last_successful_push_at is None:
            from_device = device_now - timedelta(hours=settings.initial_lookback_hours)
        else:
            from_device = utc_to_device_naive(state.last_successful_push_at, tz) - timedelta(
                seconds=settings.sync_overlap_seconds
            )
        return _Window(
            from_device=from_device,
            to_device=to_device,
            from_utc=device_naive_to_utc(from_device, tz),
            to_utc=device_naive_to_utc(to_device, tz),
        )

    def _guard_clock(
        self,
        state: SyncState,
        device_now: datetime,
        tz: ZoneInfo,
        offset_seconds: float,
        previous_offset: float | None,
        settings: SyncSettings,
    ) -> None:
        """Refuse to run on a clock that has moved. Both directions lose data.

        Backwards: the window inverts and every subsequent sync silently returns
        nothing. Forwards: the watermark leaps and the skipped interval is never
        pushed. Neither is retryable — the next attempt reads the same wrong clock.
        """
        device_now_utc = device_naive_to_utc(device_now, tz)

        if (
            state.last_successful_push_at is not None
            and device_now_utc < state.last_successful_push_at
        ):
            behind = (state.last_successful_push_at - device_now_utc).total_seconds()
            raise DeviceClockRegressionError(
                f"The device clock is {behind:.0f} seconds behind the last "
                "synchronized punch, so the sync window would be inverted. Correct "
                "the device clock, then use a manual range sync to recover the gap."
            )

        if previous_offset is not None:
            delta = abs(offset_seconds - previous_offset)
            if delta > settings.max_clock_offset_delta_seconds:
                raise DeviceClockJumpError(
                    f"The device clock moved {delta:.0f} seconds since the last "
                    "sync. Synchronization is paused for this device to avoid "
                    "skipping records; verify the device time, then run a manual "
                    "range sync over the affected period."
                )

    # ── persistence helpers ──────────────────────────────────────────────────

    def _get_state(self, db: DbSession, device_id: int) -> SyncState:
        state = db.execute(
            select(SyncState).where(SyncState.device_id == device_id)
        ).scalar_one_or_none()
        if state is None:
            state = SyncState(device_id=device_id)
            db.add(state)
            db.flush()
        return state

    def _finish_ok(
        self,
        db: DbSession,
        run: SyncRun,
        state: SyncState,
        status: SyncStatus,
        *,
        watermark: datetime | None,
    ) -> SyncOutcome:
        now = utcnow()
        run.status = status
        run.completed_at = now

        state.last_status = status
        state.last_sync_completed_at = now
        state.last_error = None

        if watermark is not None:
            # Never move the watermark backwards: a manual catch-up or an
            # out-of-order batch must not rewind automatic sync.
            if (
                state.last_successful_push_at is None
                or watermark > state.last_successful_push_at
            ):
                state.last_successful_push_at = watermark
            else:
                watermark = state.last_successful_push_at

        db.flush()
        logger.info(
            "Sync finished.",
            extra={
                "device_id": run.device_id,
                "trigger": str(run.trigger),
                "status": str(status),
                "buffer_records": run.buffer_records,
                "window_records": run.window_records,
                "sent": run.records_sent,
                "succeeded": run.records_success,
                "failed": run.records_failed,
                "suppressed": run.records_suppressed,
                "watermark": state.last_successful_push_at.isoformat()
                if state.last_successful_push_at
                else None,
            },
        )
        return _outcome(run, status, watermark)

    def _finish_failed(
        self,
        db: DbSession,
        run: SyncRun,
        state: SyncState,
        status: SyncStatus,
        message: str,
    ) -> SyncOutcome:
        """Close a failed run. The watermark is deliberately untouched (RULE 3)."""
        now = utcnow()
        run.status = status
        run.completed_at = now
        run.error_message = message

        state.last_status = status
        state.last_sync_completed_at = now
        state.last_error = message
        db.flush()

        logger.warning(
            "Sync failed.",
            extra={
                "device_id": run.device_id,
                "trigger": str(run.trigger),
                "status": str(status),
                "error": message,
                "watermark_unchanged": True,
            },
        )
        return _outcome(run, status, None, error=message)


def _split_batch(
    batch: list[BiometricPunch], result: PushResult
) -> tuple[list[BiometricPunch], list[BiometricPunch]]:
    """Partition a batch using the per-record results, if the API reported any.

    Without a results path the batch is all-or-nothing, and reaching here at all
    means HRMS returned success — so everything counts as accepted.
    """
    if not result.rejected_ids and not result.accepted_ids:
        return list(batch), []
    accepted = [p for p in batch if p.client_record_id not in result.rejected_ids]
    rejected = [p for p in batch if p.client_record_id in result.rejected_ids]
    return accepted, rejected


def _safe_watermark(
    accepted: list[BiometricPunch], rejected: list[BiometricPunch]
) -> datetime | None:
    """The watermark to advance to, honouring the contiguous-prefix rule.

    With nothing rejected, the maximum accepted punch time is safe.

    With rejections, advancing past a rejected record would strand it behind the
    watermark permanently — it would never be read again. So the watermark may move
    only if every rejected record is *later* than every accepted one; otherwise it
    does not move at all and the window is retried in full.
    """
    if not accepted:
        return None
    max_accepted = max(p.punch_time for p in accepted)
    if not rejected:
        return max_accepted
    earliest_rejected = min(p.punch_time for p in rejected)
    if earliest_rejected > max_accepted:
        return max_accepted
    return None


def _outcome(
    run: SyncRun, status: SyncStatus, watermark: datetime | None, error: str | None = None
) -> SyncOutcome:
    return SyncOutcome(
        run_id=run.id,
        device_id=run.device_id,
        status=status,
        buffer_records=run.buffer_records,
        window_records=run.window_records,
        records_sent=run.records_sent,
        records_success=run.records_success,
        records_failed=run.records_failed,
        records_invalid=run.records_invalid,
        records_suppressed=run.records_suppressed,
        watermark_advanced_to=watermark,
        error_message=error,
    )
