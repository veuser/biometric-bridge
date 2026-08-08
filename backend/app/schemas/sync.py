"""Sync API models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import SyncStatus, SyncTrigger
from app.utils.datetime_utils import ensure_aware


class ManualSyncRequest(BaseModel):
    """An explicit historical window. Never advances the watermark (RULE 5)."""

    from_time: datetime
    to_time: datetime

    @field_validator("from_time", "to_time")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        # A naive value here would be silently interpreted in some arbitrary zone
        # and read the wrong window off the device.
        return ensure_aware(value, field="time")


class SyncOutcomeResponse(BaseModel):
    run_id: int
    device_id: int
    status: SyncStatus
    buffer_records: int
    window_records: int
    records_sent: int
    records_success: int
    records_failed: int
    records_invalid: int
    records_suppressed: int
    watermark_advanced_to: datetime | None
    error_message: str | None


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    device_name: str | None = None
    trigger: SyncTrigger
    started_at: datetime
    completed_at: datetime | None
    from_time: datetime | None
    to_time: datetime | None
    buffer_records: int
    window_records: int
    records_sent: int
    records_success: int
    records_failed: int
    records_invalid: int
    records_suppressed: int
    device_read_ms: int | None
    hrms_ms: int | None
    duration_ms: int | None
    status: SyncStatus
    error_message: str | None


class SyncHistoryPage(BaseModel):
    items: list[SyncRunResponse]
    total: int
    page: int
    page_size: int


class DeviceSyncStatus(BaseModel):
    device_id: int
    device_name: str
    enabled: bool
    last_successful_push_at: datetime | None
    last_sync_started_at: datetime | None
    last_sync_completed_at: datetime | None
    last_status: SyncStatus | None
    last_error: str | None
    clock_offset_seconds: float | None
    clock_warning: str | None
    is_running: bool


class SyncStatusResponse(BaseModel):
    auto_sync_enabled: bool
    sync_interval_minutes: int
    devices: list[DeviceSyncStatus]


class SyncSettingsResponse(BaseModel):
    auto_sync_enabled: bool
    sync_interval_minutes: int = Field(ge=1, le=1440)
    sync_overlap_seconds: int = Field(ge=0, le=3600)
    initial_lookback_hours: int = Field(ge=1, le=8760)
    timezone: str
    clock_drift_warning_seconds: int
    max_clock_offset_delta_seconds: int
    manual_max_span_days: int
    stale_lock_minutes: int
    sync_run_retention_days: int
    #: Surfaced by the portal when a short interval would leave the bridge reading
    #: almost continuously — a full-buffer read measured ~7.5s per device.
    interval_warning: str | None = None
