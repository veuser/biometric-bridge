"""Operator-tunable runtime settings.

Separate from :mod:`app.core.config`, which holds deployment facts that must be known
before the database exists. These are the knobs an operator turns in the portal, and
they must take effect without restarting a Windows service.

Stored as key/value strings in ``app_settings`` and read through a typed model, so a
hand-edited or corrupted value degrades to its default rather than crashing the
scheduler.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.logging import get_logger
from app.database.models import AppSetting

logger = get_logger(__name__)

from pydantic import BaseModel, Field  # noqa: E402


class SyncSettings(BaseModel):
    """Defaults chosen from measurements, not taste — see PLAN.md §1.1."""

    auto_sync_enabled: bool = False

    #: 5 minutes, not 1. A full-buffer read measured ~7.5 s per device against the
    #: reference terminals, so a 1-minute interval would leave the bridge reading
    #: almost continuously.
    sync_interval_minutes: int = Field(default=5, ge=1, le=1440)

    #: Re-reads the boundary of the last window. Catches records written to the
    #: device buffer after a read but timestamped before its end.
    sync_overlap_seconds: int = Field(default=60, ge=0, le=3600)

    #: Bounds the very first sync so a device holding two years of history does not
    #: push its entire buffer into HRMS on install.
    initial_lookback_hours: int = Field(default=24, ge=1, le=8760)

    timezone: str = "Asia/Kolkata"

    #: Warn the operator beyond this; the bridge never corrects a device clock.
    clock_drift_warning_seconds: int = Field(default=120, ge=10, le=86400)

    #: Refuse to sync if a device clock moves further than this between runs — a
    #: jump silently strands every record in the skipped interval.
    max_clock_offset_delta_seconds: int = Field(default=300, ge=30, le=86400)

    #: Ceiling on an operator-chosen historical window.
    manual_max_span_days: int = Field(default=31, ge=1, le=3650)

    #: A stale lock older than this is treated as abandoned by a crashed process.
    stale_lock_minutes: int = Field(default=30, ge=1, le=1440)

    sync_run_retention_days: int = Field(default=180, ge=7, le=3650)


_PREFIX = "sync."


class SettingsService:
    def load(self, db: DbSession) -> SyncSettings:
        rows = db.execute(select(AppSetting)).scalars().all()
        raw = {
            row.key[len(_PREFIX) :]: row.value
            for row in rows
            if row.key.startswith(_PREFIX)
        }
        try:
            return SyncSettings.model_validate(raw)
        except Exception:
            # A corrupted row must not stop the scheduler from running at all.
            logger.exception("Stored sync settings are invalid; falling back to defaults.")
            return SyncSettings()

    def save(self, db: DbSession, settings: SyncSettings) -> SyncSettings:
        existing = {
            row.key: row
            for row in db.execute(select(AppSetting)).scalars()
            if row.key.startswith(_PREFIX)
        }
        for field, value in settings.model_dump().items():
            key = f"{_PREFIX}{field}"
            text = str(value)
            row = existing.get(key)
            if row is None:
                db.add(AppSetting(key=key, value=text))
            else:
                row.value = text
        db.flush()
        logger.info(
            "Sync settings saved.",
            extra={
                "auto_sync_enabled": settings.auto_sync_enabled,
                "interval_minutes": settings.sync_interval_minutes,
                "overlap_seconds": settings.sync_overlap_seconds,
            },
        )
        return settings
