"""Backup endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.deps import AdminSession
from app.services.backup_service import BackupService

router = APIRouter(prefix="/api/backups", tags=["backups"])


class BackupInfo(BaseModel):
    name: str
    size_bytes: int
    created_at: str


@router.get("", response_model=list[BackupInfo])
def list_backups(request: Request, _admin: AdminSession) -> list[BackupInfo]:
    service = BackupService(request.app.state.settings)
    return [BackupInfo(**service.describe(p)) for p in service.list_backups()]  # type: ignore[arg-type]


@router.post("", response_model=BackupInfo, status_code=201)
def create_backup(request: Request, _admin: AdminSession) -> BackupInfo:
    """Take a consistent snapshot now, then prune to the retention limit."""
    service = BackupService(request.app.state.settings)
    return BackupInfo(**service.describe(service.create()))  # type: ignore[arg-type]
