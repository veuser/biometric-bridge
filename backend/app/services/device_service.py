"""Device configuration management."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.core.errors import DeviceConfigurationError
from app.core.logging import get_logger
from app.database.models import Device, SyncState
from app.schemas.device import DeviceCreate, DeviceUpdate
from app.services.secret_protector import SecretProtector, get_secret_protector

logger = get_logger(__name__)

#: Beyond this the sync window arithmetic is at risk, so the portal warns.
CLOCK_DRIFT_WARNING_SECONDS = 120


class DeviceService:
    def __init__(self, protector: SecretProtector | None = None) -> None:
        self._protector = protector or get_secret_protector()

    def list_devices(self, db: DbSession) -> list[Device]:
        return list(db.execute(select(Device).order_by(Device.name)).scalars())

    def get(self, db: DbSession, device_id: int) -> Device:
        device = db.get(Device, device_id)
        if device is None:
            raise DeviceConfigurationError("Device not found.")
        return device

    def create(self, db: DbSession, payload: DeviceCreate) -> Device:
        device = Device(
            name=payload.name,
            ip_address=payload.ip_address,
            port=payload.port,
            location=payload.location,
            serial_number=payload.serial_number,
            device_type=payload.device_type,
            role=payload.role,
            timeout_seconds=payload.timeout_seconds,
            force_udp=payload.force_udp,
            timezone=payload.timezone,
            enabled=payload.enabled,
            comm_key_protected=(
                self._protector.protect(payload.comm_key) if payload.comm_key else None
            ),
        )
        db.add(device)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise DeviceConfigurationError(
                f"A device is already configured at {payload.ip_address}:{payload.port}."
            ) from exc

        # Each device owns its watermark (RULE 4), created with the device so no
        # sync path ever has to cope with a missing state row.
        db.add(SyncState(device_id=device.id))
        db.flush()

        logger.info(
            "Device created.",
            extra={"device_id": device.id, "device_name": device.name, "role": str(device.role)},
        )
        return device

    def update(self, db: DbSession, device_id: int, payload: DeviceUpdate) -> Device:
        device = self.get(db, device_id)

        device.name = payload.name
        device.ip_address = payload.ip_address
        device.port = payload.port
        device.location = payload.location
        device.serial_number = payload.serial_number
        device.device_type = payload.device_type
        device.role = payload.role
        device.timeout_seconds = payload.timeout_seconds
        device.force_udp = payload.force_udp
        device.timezone = payload.timezone
        device.enabled = payload.enabled

        # An omitted comm_key means "leave it alone", not "clear it" — otherwise
        # every edit through the portal would silently wipe the key, since the form
        # cannot pre-fill a write-only field.
        #
        # Which is exactly why removal needs its own flag. Most ZK terminals ship with
        # no comm key at all, so a value typed in by mistake breaks every sync with
        # "rejected the communication key" — and the field an operator would clear to
        # fix it is the one that means "keep". The flag is the only way out that does
        # not involve editing the database.
        if payload.clear_comm_key:
            device.comm_key_protected = None
        elif payload.comm_key:
            device.comm_key_protected = self._protector.protect(payload.comm_key)

        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise DeviceConfigurationError(
                f"A device is already configured at {payload.ip_address}:{payload.port}."
            ) from exc

        logger.info("Device updated.", extra={"device_id": device.id, "device_name": device.name})
        return device

    def delete(self, db: DbSession, device_id: int) -> None:
        device = self.get(db, device_id)
        name = device.name
        db.delete(device)
        db.flush()
        logger.info("Device deleted.", extra={"device_id": device_id, "device_name": name})

    def set_enabled(self, db: DbSession, device_id: int, enabled: bool) -> Device:
        device = self.get(db, device_id)
        device.enabled = enabled
        db.flush()
        logger.info(
            "Device %s.", "enabled" if enabled else "disabled", extra={"device_id": device_id}
        )
        return device


def clock_warning(offset_seconds: float | None, device_name: str | None = None) -> str | None:
    """Human-readable warning when a device clock has drifted too far.

    The bridge never corrects a device clock — that is an operator action on the
    terminal — but it must say so loudly, because drift silently shifts every punch
    it reports.

    ``device_name`` is included when the caller knows it, because these warnings are
    rendered as a stack on the dashboard: with two terminals they read as near-identical
    sentences differing only by a number, and with five across sites there is nothing to
    tell the operator which terminal to go and fix. A warning that does not name its
    subject is an alert someone has to do detective work on.
    """
    if offset_seconds is None:
        return None
    if abs(offset_seconds) <= CLOCK_DRIFT_WARNING_SECONDS:
        return None
    direction = "ahead of" if offset_seconds > 0 else "behind"
    subject = f"{device_name} clock" if device_name else "Device clock"
    return (
        f"{subject} is {abs(offset_seconds):.0f} seconds {direction} this server. "
        "Every punch is recorded with that offset, and one made within "
        f"{abs(offset_seconds):.0f} seconds of midnight will be filed on the wrong "
        "day — attendance is grouped by date, so a shifted punch lands on a different "
        "day's record and stops pairing with its check-in or check-out. Correct the "
        "clock on the terminal; the bridge will not adjust it."
    )
