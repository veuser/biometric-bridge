"""Device API models.

The device communication key is write-only: it can be set, but no response model
ever returns it.
"""

from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address as parse_ip

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import DeviceRole, DeviceType


class DeviceBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ip_address: str = Field(min_length=1, max_length=45)
    port: int = Field(default=4370, ge=1, le=65535)
    location: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=64)
    device_type: DeviceType = DeviceType.ESSL
    role: DeviceRole = DeviceRole.CHECK_IN
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    force_udp: bool = False
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool = True

    @field_validator("ip_address")
    @classmethod
    def _must_be_an_ip(cls, value: str) -> str:
        # Rejected here rather than at connect time so a typo surfaces while the
        # operator is still looking at the form.
        try:
            parse_ip(value)
        except ValueError as exc:
            raise ValueError("Enter a valid IP address.") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_must_resolve(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone {value!r}.") from exc
        return value


def _validate_comm_key(value: str | None) -> str | None:
    """ZK communication keys are numeric.

    Rejected at the form, alongside the IP, rather than surfacing later as a
    puzzling "device rejected the request" during a sync.
    """
    if value is None or value == "":
        return None
    if not value.isdigit():
        raise ValueError("The device communication key must be numeric.")
    return value


class DeviceCreate(DeviceBase):
    #: Write-only. Never echoed back by any response model.
    comm_key: str | None = Field(default=None, max_length=64)

    @field_validator("comm_key")
    @classmethod
    def _check_comm_key(cls, value: str | None) -> str | None:
        return _validate_comm_key(value)


class DeviceUpdate(DeviceCreate):
    pass


class DeviceResponse(DeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    comm_key_set: bool = False


class DeviceTestRequest(BaseModel):
    """Test an unsaved device straight from the add/edit form."""

    ip_address: str
    port: int = Field(default=4370, ge=1, le=65535)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    comm_key: str | None = Field(default=None, max_length=64)
    force_udp: bool = False
    name: str = "device"

    @field_validator("comm_key")
    @classmethod
    def _check_comm_key(cls, value: str | None) -> str | None:
        return _validate_comm_key(value)


class DeviceInfoResponse(BaseModel):
    serial_number: str | None = None
    firmware_version: str | None = None
    platform: str | None = None
    device_name: str | None = None
    user_count: int | None = None
    record_count: int | None = None
    device_time: datetime | None = None


class DeviceTestResponse(BaseModel):
    reachable: bool
    message: str
    info: DeviceInfoResponse | None = None
    clock_offset_seconds: float | None = None
    #: Set when the device clock differs enough to threaten window arithmetic.
    clock_warning: str | None = None
