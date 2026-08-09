"""Wires a sync run together: lock, device, HRMS client, settings, engine.

Kept separate from :class:`~app.services.sync_service.SyncService` so the engine can
be tested against fakes without a database session, an HTTP transport, or a lock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.biometric.device_manager import build_device
from app.core.enums import SyncStatus, SyncTrigger
from app.core.errors import BridgeError, DeviceConfigurationError, HrmsNotConfiguredError
from app.core.logging import get_logger
from app.database.models import Device, SyncRun
from app.services.hrms_service import HrmsService
from app.services.settings_service import SettingsService, SyncSettings
from app.services.sync_lock import device_lock
from app.services.sync_service import SyncOutcome, SyncService
from app.utils.datetime_utils import utcnow

logger = get_logger(__name__)


class SyncRunner:
    def __init__(
        self,
        *,
        sync_service: SyncService | None = None,
        hrms_service: HrmsService | None = None,
        settings_service: SettingsService | None = None,
    ) -> None:
        self.sync_service = sync_service or SyncService()
        self.hrms_service = hrms_service or HrmsService()
        self.settings_service = settings_service or SettingsService()

    def run_for_device(
        self,
        db: DbSession,
        device_record: Device,
        trigger: SyncTrigger,
        *,
        manual_from: datetime | None = None,
        manual_to: datetime | None = None,
    ) -> SyncOutcome:
        settings = self.settings_service.load(db)

        if not device_record.enabled:
            raise DeviceConfigurationError(
                f"Device {device_record.name!r} is disabled."
            )

        if trigger is SyncTrigger.MANUAL_RANGE:
            _validate_manual_window(manual_from, manual_to, settings)

        with device_lock(
            db,
            device_record.id,
            stale_after=timedelta(minutes=settings.stale_lock_minutes),
        ):
            device = build_device(device_record)

            hrms_client = None
            api_key = None
            tenant = None
            hrms_error = None
            config = self.hrms_service.get_config(db)
            if config is None or not config.enabled:
                hrms_error = (
                    "HRMS synchronization is switched off, so there is nowhere to send "
                    "these punches. Tick 'Enable synchronization to HRMS' on the HRMS "
                    "Configuration page."
                )
            else:
                try:
                    api_key = self.hrms_service.reveal_api_key(config)
                    hrms_client = self.hrms_service.build_client(config)
                    tenant = config.tenant_identifier
                except BridgeError as exc:
                    # Swallowed on purpose: a run that read no punches should still
                    # succeed as NO_RECORDS rather than fail over a destination it never
                    # needed. But if there ARE punches, the engine raises — and it needs
                    # this reason to say which piece is missing instead of a generic
                    # "not configured".
                    hrms_error = exc.message
                    logger.warning(
                        "HRMS is configured but unusable.", extra={"error": exc.message}
                    )

            return self.sync_service.run_sync(
                db,
                device_record=device_record,
                device=device,
                hrms_client=hrms_client,
                api_key=api_key,
                tenant=tenant,
                hrms_error=hrms_error,
                settings=settings,
                trigger=trigger,
                manual_from=manual_from,
                manual_to=manual_to,
            )

    def run_all_enabled(self, db: DbSession, trigger: SyncTrigger) -> list[SyncOutcome]:
        """Sync every enabled device.

        One device failing must never stop the others — a single unreachable
        terminal cannot be allowed to halt attendance collection site-wide.
        """
        devices = list(
            db.execute(select(Device).where(Device.enabled.is_(True)).order_by(Device.id))
            .scalars()
        )
        outcomes: list[SyncOutcome] = []
        for device_record in devices:
            try:
                outcomes.append(self.run_for_device(db, device_record, trigger))
            except BridgeError as exc:
                # Reported, never dropped. Returning one fewer row than there are
                # enabled devices makes a skipped device indistinguishable from one
                # that does not exist — the caller sees a complete-looking result and
                # a terminal that is quietly not being collected. A device already
                # holding its lock (scheduler mid-cycle, or a second click) reaches
                # here on the ordinary path, so this is not a rare branch.
                logger.warning(
                    "Skipping device.",
                    extra={"device_id": device_record.id, "error": exc.message},
                )
                outcomes.append(self._skipped(device_record.id, exc.message))
            except Exception as exc:
                logger.exception(
                    "Unexpected error syncing device.",
                    extra={"device_id": device_record.id},
                )
                # Type name only: an unexpected exception's message can carry
                # connection strings or paths, and this one is rendered in the portal.
                outcomes.append(
                    self._skipped(device_record.id, f"Unexpected error: {type(exc).__name__}")
                )
        return outcomes

    @staticmethod
    def _skipped(device_id: int, reason: str) -> SyncOutcome:
        """An outcome for a device that was never attempted.

        ``run_id`` is 0 because no ``sync_runs`` row exists — nothing ran. Sync History
        is a record of executions and must not gain phantom entries for work that did
        not happen; this exists only so the immediate result accounts for every device.
        """
        return SyncOutcome(
            run_id=0,
            device_id=device_id,
            status=SyncStatus.SKIPPED,
            error_message=reason,
        )


def _validate_manual_window(
    manual_from: datetime | None, manual_to: datetime | None, settings: SyncSettings
) -> None:
    if manual_from is None or manual_to is None:
        raise DeviceConfigurationError("A manual sync needs both a start and an end time.")
    if manual_from >= manual_to:
        raise DeviceConfigurationError("The start time must be before the end time.")
    span = manual_to - manual_from
    if span > timedelta(days=settings.manual_max_span_days):
        raise DeviceConfigurationError(
            f"The window is longer than the {settings.manual_max_span_days}-day maximum. "
            "Run it in smaller pieces."
        )
    if manual_to > utcnow() + timedelta(minutes=5):
        raise DeviceConfigurationError("The end time cannot be in the future.")


def close_interrupted_runs(db: DbSession) -> int:
    """Startup recovery for runs killed mid-flight.

    A ``RUNNING`` row from a previous process cannot still be running — nothing is
    executing it. Left alone it would sit in the history forever and make the
    dashboard claim a sync is in progress (PLAN.md §43.10/11).

    The watermark is deliberately untouched: whether HRMS accepted those punches is
    unknown, and the safe assumption is that it did not.
    """
    runs = list(
        db.execute(select(SyncRun).where(SyncRun.status == SyncStatus.RUNNING)).scalars()
    )
    now = utcnow()
    for run in runs:
        run.status = SyncStatus.INTERRUPTED
        run.completed_at = now
        run.error_message = "Interrupted — the service stopped while this sync was running."
    if runs:
        logger.warning("Closed interrupted sync runs from a previous process.", extra={
            "count": len(runs)
        })
    db.flush()
    return len(runs)


def require_sync_prerequisites(db: DbSession, hrms_service: HrmsService) -> None:
    """Fail fast with an actionable message rather than mid-run."""
    config = hrms_service.get_config(db)
    if config is None or not config.enabled:
        raise HrmsNotConfiguredError(
            "HRMS synchronization is not enabled. Configure it on the HRMS "
            "Configuration page first."
        )
