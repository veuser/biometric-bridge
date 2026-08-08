"""Automatic synchronization and housekeeping.

APScheduler with an in-memory job store. Jobs are re-registered from settings on
every start, so there is no persisted schedule to drift out of step with the
configuration an operator sees in the portal.

Three settings matter and are set deliberately:

* ``max_instances=1`` — a cycle that overruns its interval must not start a second
  one. The per-device lock would refuse it anyway, but the scheduler should not be
  generating work it knows will be rejected.
* ``coalesce=True`` — after the machine sleeps or the service is stopped for an hour,
  run once on resume rather than twelve times in a row.
* ``misfire_grace_time`` — a cycle delayed past its slot still runs, instead of being
  dropped silently.
"""

from __future__ import annotations

import anyio.to_thread
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.enums import SyncTrigger
from app.core.logging import get_logger
from app.database.database import DatabaseManager
from app.services.settings_service import SettingsService, SyncSettings
from app.services.sync_runner import SyncRunner
from app.services.sync_service import SyncOutcome

logger = get_logger(__name__)

SYNC_JOB_ID = "biometric-sync"
RETENTION_JOB_ID = "sync-run-retention"

#: A cycle delayed past its slot still runs, rather than vanishing.
MISFIRE_GRACE_SECONDS = 120


class SyncScheduler:
    def __init__(
        self,
        db: DatabaseManager,
        *,
        runner: SyncRunner | None = None,
        settings_service: SettingsService | None = None,
    ) -> None:
        self._db = db
        self._runner = runner or SyncRunner()
        self._settings_service = settings_service or SettingsService()
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._scheduler.start()
        self.reload()
        logger.info("Scheduler started.")

    def shutdown(self) -> None:
        if self._scheduler.running:
            # wait=False: a sync in flight holds a device lock that startup
            # recovery will clear. Blocking service shutdown for a 7-second buffer
            # read risks Windows killing the process mid-write instead.
            self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running)

    def reload(self) -> None:
        """Re-register jobs from current settings. Safe to call at any time.

        Called after a settings change so a new interval takes effect immediately,
        without restarting the Windows service.
        """
        with self._db.session() as session:
            settings = self._settings_service.load(session)

        self._scheduler.remove_all_jobs()

        if settings.auto_sync_enabled:
            self._scheduler.add_job(
                self._run_cycle,
                trigger=IntervalTrigger(minutes=settings.sync_interval_minutes),
                id=SYNC_JOB_ID,
                name="Biometric attendance sync",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=MISFIRE_GRACE_SECONDS,
                replace_existing=True,
            )
            logger.info(
                "Automatic sync scheduled.",
                extra={"interval_minutes": settings.sync_interval_minutes},
            )
        else:
            logger.info("Automatic sync is disabled; no sync job registered.")

        self._scheduler.add_job(
            self._prune_history,
            trigger=IntervalTrigger(hours=24),
            id=RETENTION_JOB_ID,
            name="Sync history retention",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    def next_run_time(self) -> str | None:
        job = self._scheduler.get_job(SYNC_JOB_ID)
        if job is None or job.next_run_time is None:
            return None
        return str(job.next_run_time.isoformat())

    # ── jobs ─────────────────────────────────────────────────────────────────

    async def _run_cycle(self) -> None:
        """One scheduled sync across all enabled devices.

        Runs in a worker thread: the whole chain below it is synchronous, and a
        full-buffer device read measured ~7.5 s on the reference terminals. Doing
        that on the event loop would freeze the portal on every cycle.

        Never raises. An exception escaping here would be swallowed by APScheduler
        and the operator would see a scheduler that silently stopped working.
        """
        try:
            outcomes = await anyio.to_thread.run_sync(self._run_cycle_blocking)
        except Exception:
            logger.exception("Scheduled sync cycle failed.")
            return

        if outcomes:
            logger.info(
                "Scheduled sync cycle finished.",
                extra={
                    "devices": len(outcomes),
                    "pushed": sum(o.records_success for o in outcomes),
                    "failed": sum(o.records_failed for o in outcomes),
                },
            )

    def _run_cycle_blocking(self) -> list[SyncOutcome]:
        with self._db.session() as session:
            return list(self._runner.run_all_enabled(session, SyncTrigger.SCHEDULED))

    async def _prune_history(self) -> None:
        try:
            removed = await anyio.to_thread.run_sync(self._prune_history_blocking)
        except Exception:
            logger.exception("Sync history retention job failed.")
            return
        if removed:
            logger.info("Pruned old sync runs.", extra={"removed": removed})

    def _prune_history_blocking(self) -> int:
        from datetime import timedelta

        from sqlalchemy import delete

        from app.database.models import SyncRun
        from app.utils.datetime_utils import utcnow

        with self._db.session() as session:
            settings = self._settings_service.load(session)
            cutoff = utcnow() - timedelta(days=settings.sync_run_retention_days)
            result = session.execute(delete(SyncRun).where(SyncRun.started_at < cutoff))
            return int(result.rowcount or 0)


def interval_warning(settings: SyncSettings, device_count: int) -> str | None:
    """Warn when the interval leaves little headroom for the buffer read.

    The ZK protocol has no server-side time filter, so every cycle transfers each
    device's whole attendance buffer — measured at ~7.5 s per device with ~20k
    records (PLAN.md §1.2). At a 1-minute interval with two devices that is a
    quarter of every minute spent reading.
    """
    if device_count == 0:
        return None
    estimated_seconds = device_count * 8
    cycle_seconds = settings.sync_interval_minutes * 60
    if estimated_seconds * 4 <= cycle_seconds:
        return None
    return (
        f"With {device_count} device(s), a sync cycle is expected to take around "
        f"{estimated_seconds}s because the whole device buffer must be transferred "
        f"each time. A {settings.sync_interval_minutes}-minute interval leaves "
        "little headroom; 5 minutes or more is recommended."
    )
