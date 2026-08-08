"""Sync settings endpoints.

Saving reloads the scheduler in place, so a changed interval takes effect without
restarting the Windows service.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import AdminSession, CurrentSession, Db
from app.database.models import Device
from app.scheduler.scheduler import interval_warning
from app.schemas.sync import SyncSettingsResponse
from app.services.settings_service import SettingsService, SyncSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])

_service = SettingsService()


def _to_response(settings: SyncSettings, device_count: int) -> SyncSettingsResponse:
    return SyncSettingsResponse(
        **settings.model_dump(),
        interval_warning=interval_warning(settings, device_count),
    )


@router.get("", response_model=SyncSettingsResponse)
def get_settings(request: Request, db: Db, _session: CurrentSession) -> SyncSettingsResponse:
    device_count = db.execute(
        select(func.count()).select_from(Device).where(Device.enabled.is_(True))
    ).scalar_one()
    return _to_response(_service.load(db), device_count)


@router.put("", response_model=SyncSettingsResponse)
def save_settings(
    payload: SyncSettings, request: Request, db: Db, _admin: AdminSession
) -> SyncSettingsResponse:
    saved = _service.save(db, payload)

    # Commit before reloading: the scheduler opens its own session to read the new
    # settings and would otherwise see the old ones.
    db.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.reload()

    device_count = db.execute(
        select(func.count()).select_from(Device).where(Device.enabled.is_(True))
    ).scalar_one()
    return _to_response(saved, device_count)
