"""Dashboard.

Every number here comes from ``sync_runs`` metadata. No punch is stored to produce a
statistic (RULE 1), which is why the dashboard talks about *records pushed* rather
than *attendance*.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentSession, Db
from app.core.enums import SyncStatus
from app.database.models import Device, HrmsConnection, SyncRun, SyncState
from app.hrms.profile import HrmsProfile
from app.services.device_service import clock_warning
from app.services.settings_service import SettingsService
from app.utils.datetime_utils import utcnow

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_settings = SettingsService()


class DeviceSummary(BaseModel):
    total: int
    enabled: int
    disabled: int
    with_clock_warning: int
    running: int


class HrmsSummary(BaseModel):
    configured: bool
    enabled: bool
    profile_configured: bool
    api_key_set: bool
    base_url: str | None


class SchedulerSummary(BaseModel):
    running: bool
    auto_sync_enabled: bool
    interval_minutes: int
    next_run_at: str | None


class ActivitySummary(BaseModel):
    """Last 24 hours, from sync-run metadata only."""

    runs: int
    records_read: int
    records_pushed: int
    records_failed: int
    failed_runs: int


class DashboardResponse(BaseModel):
    devices: DeviceSummary
    hrms: HrmsSummary
    scheduler: SchedulerSummary
    activity: ActivitySummary
    last_successful_sync_at: str | None
    last_failed_sync_at: str | None
    last_error: str | None
    warnings: list[str]


@router.get("", response_model=DashboardResponse)
def dashboard(request: Request, db: Db, _session: CurrentSession) -> DashboardResponse:
    settings = _settings.load(db)
    states = list(db.execute(select(SyncState)).scalars())

    total = db.execute(select(func.count()).select_from(Device)).scalar_one()
    enabled = db.execute(
        select(func.count()).select_from(Device).where(Device.enabled.is_(True))
    ).scalar_one()

    # Names come from one query rather than a lazy load per state: these warnings are
    # rendered as a stack, and without the device name two terminals produce sentences
    # that differ only by a number.
    device_names = dict(db.execute(select(Device.id, Device.name)).all())

    warnings: list[str] = []
    clock_warnings = 0
    for state in states:
        warning = clock_warning(state.clock_offset_seconds, device_names.get(state.device_id))
        if warning:
            clock_warnings += 1
            warnings.append(warning)

    config = db.execute(select(HrmsConnection).limit(1)).scalar_one_or_none()
    profile_configured = False
    if config is not None and config.profile_json:
        try:
            profile_configured = HrmsProfile.model_validate_json(
                config.profile_json
            ).is_configured
        except ValueError:
            profile_configured = False

    if config is None or not config.enabled:
        warnings.append("HRMS synchronization is not enabled; nothing is being pushed.")
    elif not profile_configured:
        warnings.append(
            "The HRMS attendance endpoint has not been set, so sync cannot run."
        )

    if not settings.auto_sync_enabled:
        warnings.append("Automatic synchronization is switched off.")

    since = utcnow() - timedelta(hours=24)
    activity_row = db.execute(
        select(
            func.count(SyncRun.id),
            func.coalesce(func.sum(SyncRun.window_records), 0),
            func.coalesce(func.sum(SyncRun.records_success), 0),
            func.coalesce(func.sum(SyncRun.records_failed), 0),
        ).where(SyncRun.started_at >= since)
    ).one()

    failed_runs = db.execute(
        select(func.count())
        .select_from(SyncRun)
        .where(SyncRun.started_at >= since)
        .where(SyncRun.status.notin_([s for s in SyncStatus if s.is_success]))
        .where(SyncRun.status != SyncStatus.RUNNING)
    ).scalar_one()

    last_success = db.execute(
        select(SyncRun.completed_at)
        .where(SyncRun.status.in_([s for s in SyncStatus if s.is_success]))
        .order_by(SyncRun.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    last_failure = db.execute(
        select(SyncRun)
        .where(SyncRun.status.notin_([s for s in SyncStatus if s.is_success]))
        .where(SyncRun.status != SyncStatus.RUNNING)
        .order_by(SyncRun.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    scheduler = getattr(request.app.state, "scheduler", None)

    return DashboardResponse(
        devices=DeviceSummary(
            total=total,
            enabled=enabled,
            disabled=total - enabled,
            with_clock_warning=clock_warnings,
            running=sum(1 for s in states if s.locked_at is not None),
        ),
        hrms=HrmsSummary(
            # "Configured" means there is somewhere to send and something to send with —
            # NOT merely that a row exists. Since the contract profile is now seeded on
            # first run, a row exists from the moment the service starts, and a
            # row-existence test would report a brand-new install as configured. A green
            # dashboard on a bridge that has never been pointed at anything is a lie.
            configured=bool(config and config.base_url and config.api_key_protected),
            enabled=bool(config and config.enabled),
            profile_configured=profile_configured,
            api_key_set=bool(config and config.api_key_protected),
            base_url=config.base_url if config else None,
        ),
        scheduler=SchedulerSummary(
            running=bool(scheduler and scheduler.running),
            auto_sync_enabled=settings.auto_sync_enabled,
            interval_minutes=settings.sync_interval_minutes,
            next_run_at=scheduler.next_run_time() if scheduler else None,
        ),
        activity=ActivitySummary(
            runs=activity_row[0],
            records_read=activity_row[1],
            records_pushed=activity_row[2],
            records_failed=activity_row[3],
            failed_runs=failed_runs,
        ),
        last_successful_sync_at=last_success.isoformat() if last_success else None,
        last_failed_sync_at=(
            last_failure.completed_at.isoformat()
            if last_failure and last_failure.completed_at
            else None
        ),
        last_error=last_failure.error_message if last_failure else None,
        warnings=warnings,
    )
