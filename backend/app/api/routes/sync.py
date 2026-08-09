"""Synchronization endpoints.

Paths are named after their **watermark semantics** rather than after who triggers
them, so the dangerous operation cannot be invoked by accident from a "Sync Now"
button (PLAN.md §5.7, spec RULE 5):

* ``/catch-up``      — same window as the scheduler, advances the watermark.
* ``/manual-range``  — an operator-chosen historical window, never touches it.
"""

from __future__ import annotations

from dataclasses import asdict

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.api.deps import AdminSession, CurrentSession, Db
from app.core.enums import SyncStatus, SyncTrigger
from app.core.errors import BridgeError, DeviceConfigurationError, SyncInProgressError
from app.database.models import Device, SyncRun, SyncState
from app.schemas.sync import (
    DeviceSyncStatus,
    ManualSyncRequest,
    SyncHistoryPage,
    SyncOutcomeResponse,
    SyncRunResponse,
    SyncStatusResponse,
)
from app.services.device_service import clock_warning
from app.services.settings_service import SettingsService
from app.services.sync_runner import SyncRunner

router = APIRouter(prefix="/api/sync", tags=["sync"])

_runner = SyncRunner()
_settings = SettingsService()


def _outcome_response(outcome: object) -> SyncOutcomeResponse:
    return SyncOutcomeResponse(**asdict(outcome))  # type: ignore[call-overload]


def _run_in_thread(db: DbSession, device_id: int, trigger: SyncTrigger, **kwargs: object):  # type: ignore[no-untyped-def]
    device = db.get(Device, device_id)
    if device is None:
        raise DeviceConfigurationError("Device not found.")
    return _runner.run_for_device(db, device, trigger, **kwargs)  # type: ignore[arg-type]


@router.post("/catch-up", response_model=list[SyncOutcomeResponse])
async def catch_up_all(db: Db, _admin: AdminSession) -> list[SyncOutcomeResponse]:
    """Run the scheduled sync now for every enabled device. Advances watermarks."""
    outcomes = await anyio.to_thread.run_sync(
        lambda: _runner.run_all_enabled(db, SyncTrigger.CATCH_UP)
    )
    return [_outcome_response(o) for o in outcomes]


@router.post("/catch-up/{device_id}", response_model=SyncOutcomeResponse)
async def catch_up_device(device_id: int, db: Db, _admin: AdminSession) -> SyncOutcomeResponse:
    try:
        outcome = await anyio.to_thread.run_sync(
            lambda: _run_in_thread(db, device_id, SyncTrigger.CATCH_UP)
        )
    except SyncInProgressError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except BridgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return _outcome_response(outcome)


@router.post("/manual-range/{device_id}", response_model=SyncOutcomeResponse)
async def manual_range(
    device_id: int, payload: ManualSyncRequest, db: Db, _admin: AdminSession
) -> SyncOutcomeResponse:
    """Push a historical window. Leaves ``last_successful_push_at`` untouched."""
    try:
        outcome = await anyio.to_thread.run_sync(
            lambda: _run_in_thread(
                db,
                device_id,
                SyncTrigger.MANUAL_RANGE,
                manual_from=payload.from_time,
                manual_to=payload.to_time,
            )
        )
    except SyncInProgressError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=exc.message) from exc
    except BridgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return _outcome_response(outcome)


@router.get("/status", response_model=SyncStatusResponse)
def sync_status(db: Db, _session: CurrentSession) -> SyncStatusResponse:
    settings = _settings.load(db)
    rows = db.execute(
        select(Device, SyncState).outerjoin(SyncState, SyncState.device_id == Device.id)
        .order_by(Device.name)
    ).all()

    devices = []
    for device, state in rows:
        devices.append(
            DeviceSyncStatus(
                device_id=device.id,
                device_name=device.name,
                enabled=device.enabled,
                last_successful_push_at=state.last_successful_push_at if state else None,
                last_sync_started_at=state.last_sync_started_at if state else None,
                last_sync_completed_at=state.last_sync_completed_at if state else None,
                last_status=state.last_status if state else None,
                last_error=state.last_error if state else None,
                clock_offset_seconds=state.clock_offset_seconds if state else None,
                clock_warning=clock_warning(state.clock_offset_seconds, device.name) if state else None,
                is_running=bool(state and state.locked_at is not None),
            )
        )

    return SyncStatusResponse(
        auto_sync_enabled=settings.auto_sync_enabled,
        sync_interval_minutes=settings.sync_interval_minutes,
        devices=devices,
    )


@router.get("/history", response_model=SyncHistoryPage)
def sync_history(
    db: Db,
    _session: CurrentSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    device_id: int | None = None,
    run_status: SyncStatus | None = None,
    trigger: SyncTrigger | None = None,
) -> SyncHistoryPage:
    """Execution metadata only — never the punches themselves (RULE 1)."""
    query = select(SyncRun, Device.name).join(Device, Device.id == SyncRun.device_id)
    count_query = select(func.count()).select_from(SyncRun)

    if device_id is not None:
        query = query.where(SyncRun.device_id == device_id)
        count_query = count_query.where(SyncRun.device_id == device_id)
    if run_status is not None:
        query = query.where(SyncRun.status == run_status)
        count_query = count_query.where(SyncRun.status == run_status)
    if trigger is not None:
        query = query.where(SyncRun.trigger == trigger)
        count_query = count_query.where(SyncRun.trigger == trigger)

    total = db.execute(count_query).scalar_one()
    rows = db.execute(
        query.order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = []
    for run, device_name in rows:
        item = SyncRunResponse.model_validate(run)
        item.device_name = device_name
        item.duration_ms = run.duration_ms
        items.append(item)

    return SyncHistoryPage(items=items, total=total, page=page, page_size=page_size)
