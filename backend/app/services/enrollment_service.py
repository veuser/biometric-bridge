"""Register HRMS employees on the terminals — add-only.

Triggered by an operator pressing **Sync new employees**; there is no schedule. HRMS says
who should be on the terminals, each terminal says who is already there, and the bridge adds
the difference. Because "new" is worked out from the terminals themselves, nothing has to be
tracked between runs, and a user that went missing from one terminal (deleted, or never
copied) is added back the same way.

What this module will **not** do, deliberately:

* **Update or delete a terminal user.** A user already on a terminal is left exactly as
  found, even if HRMS has a different name or card for them. The only write is "add a user
  that is absent".
* **Overwrite a slot.** The ZK protocol's ``set_user`` replaces whatever occupies the slot it
  is given, so every add goes into a slot proven unused by reading the terminal first.
* **Store employee data.** Names and codes pass through memory for the length of the run and
  are returned to the operator; only counts are logged.
* **Grant privilege.** Users are added as ordinary users, never administrators.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.biometric.base import BiometricDevice, DeviceUserRecord, EnrollableDevice
from app.biometric.device_manager import build_device
from app.biometric.essl_client import MAX_USER_ID_LENGTH
from app.core.errors import BridgeError, DeviceError, HrmsNotConfiguredError
from app.core.logging import get_logger
from app.database.models import Device
from app.hrms.client import HrmsEmployee
from app.services.hrms_service import HrmsService
from app.services.settings_service import SettingsService
from app.services.sync_lock import device_lock

logger = get_logger(__name__)

#: The terminal stores the slot number in 16 bits.
MAX_UID = 65_535
#: The terminal stores the card number in 32 bits.
MAX_CARD = 4_294_967_295


@dataclass(slots=True)
class PlannedAdd:
    uid: int
    employee_code: str
    name: str
    card: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkippedEmployee:
    employee_code: str
    reason: str


@dataclass(slots=True)
class AddPlan:
    to_add: list[PlannedAdd] = field(default_factory=list)
    already_present: int = 0
    skipped: list[SkippedEmployee] = field(default_factory=list)


def normalize_card(raw: str | None) -> tuple[str | None, str | None]:
    """``(card, warning)``. A card that cannot be stored is dropped, never guessed at."""
    if raw is None or not raw.strip():
        return None, None
    text = raw.strip()
    if not text.isdigit():
        return None, f"Card number {text!r} is not numeric, so it was not set."
    value = int(text)
    if value == 0:
        return None, None  # 0 means "no card" on the terminal
    if value > MAX_CARD:
        return None, f"Card number {text!r} is too large for the terminal, so it was not set."
    return str(value), None


def plan_additions(
    employees: list[HrmsEmployee], existing: list[DeviceUserRecord]
) -> AddPlan:
    """What to add to one terminal. Pure: no I/O, so every rule is testable on its own."""
    plan = AddPlan()

    present_codes = {u.user_id.strip().lower() for u in existing}
    used_uids = {u.uid for u in existing}
    used_cards = {u.card for u in existing if u.card}
    seen: set[str] = set()
    next_uid = 1

    for employee in employees:
        code = employee.employee_code.strip()
        key = code.lower()

        if key in seen:
            continue  # the same employee listed twice; add once
        seen.add(key)

        if key in present_codes:
            plan.already_present += 1
            continue

        if len(code) > MAX_USER_ID_LENGTH:
            plan.skipped.append(
                SkippedEmployee(
                    code,
                    f"The employee code is longer than the {MAX_USER_ID_LENGTH} characters "
                    "the terminal can store.",
                )
            )
            continue

        while next_uid in used_uids:
            next_uid += 1
        if next_uid > MAX_UID:
            plan.skipped.append(
                SkippedEmployee(code, "The terminal has no free user slot left.")
            )
            continue

        warnings: list[str] = []
        card, warning = normalize_card(employee.card_number)
        if warning:
            warnings.append(warning)
        if card is not None and card in used_cards:
            # Two people on one card would mean the wrong person is credited. Add the
            # employee without the card and let an operator resolve the clash.
            warnings.append(
                f"Card number {card} is already assigned to another user on this "
                "terminal, so it was not set."
            )
            card = None
        if card is not None:
            used_cards.add(card)

        used_uids.add(next_uid)
        plan.to_add.append(
            PlannedAdd(
                uid=next_uid,
                employee_code=code,
                name=employee.name.strip() or code,
                card=card,
                warnings=warnings,
            )
        )

    return plan


# ── outcome ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class AddedUser:
    employee_code: str
    name: str
    card_set: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FailedUser:
    employee_code: str
    reason: str


@dataclass(slots=True)
class DeviceEnrollmentOutcome:
    device_id: int
    device_name: str
    #: ``OK`` (everything wanted is on the terminal), ``PARTIAL`` (some adds failed),
    #: ``FAILED`` (the terminal could not be used) or ``SKIPPED`` (not attempted).
    status: str
    dry_run: bool
    #: In a dry run these are the users that WOULD be added; nothing was written.
    added: list[AddedUser] = field(default_factory=list)
    already_present: int = 0
    skipped: list[FailedUser] = field(default_factory=list)
    failed: list[FailedUser] = field(default_factory=list)
    error_message: str | None = None


@dataclass(slots=True)
class EnrollmentOutcome:
    dry_run: bool
    employees_in_hrms: int
    devices: list[DeviceEnrollmentOutcome] = field(default_factory=list)


# ── runner ────────────────────────────────────────────────────────────────────

DeviceFactory = Callable[[Device], BiometricDevice]


class EnrollmentRunner:
    def __init__(
        self,
        *,
        hrms_service: HrmsService | None = None,
        settings_service: SettingsService | None = None,
        device_factory: DeviceFactory = build_device,
    ) -> None:
        self._hrms = hrms_service or HrmsService()
        self._settings = settings_service or SettingsService()
        self._device_factory = device_factory

    def run(self, db: DbSession, *, dry_run: bool) -> EnrollmentOutcome:
        """Fetch the employee list, then reconcile every enabled terminal against it.

        An HRMS failure raises: with no list there is nothing to reconcile, and a
        silent empty result would read as "everyone is already registered".
        """
        employees = self.fetch_employees(db)
        return self.reconcile_devices(db, employees, dry_run=dry_run)

    def fetch_employees(self, db: DbSession) -> list[HrmsEmployee]:
        config = self._hrms.require_config(db)
        if not config.base_url:
            raise HrmsNotConfiguredError("The HRMS base URL has not been set.")
        api_key = self._hrms.reveal_api_key(config)
        client = self._hrms.build_client(config)
        return client.list_employees(api_key=api_key, tenant=config.tenant_identifier)

    def reconcile_devices(
        self, db: DbSession, employees: list[HrmsEmployee], *, dry_run: bool
    ) -> EnrollmentOutcome:
        outcome = EnrollmentOutcome(dry_run=dry_run, employees_in_hrms=len(employees))
        stale_after = timedelta(minutes=self._settings.load(db).stale_lock_minutes)

        devices = list(
            db.execute(select(Device).where(Device.enabled.is_(True)).order_by(Device.id))
            .scalars()
        )
        for record in devices:
            # One terminal failing must never stop the other: an unreachable Entry
            # device is no reason to leave the Exit device unregistered.
            try:
                with device_lock(db, record.id, stale_after=stale_after):
                    result = self._reconcile_one(record, employees, dry_run=dry_run)
            except BridgeError as exc:
                result = DeviceEnrollmentOutcome(
                    device_id=record.id,
                    device_name=record.name,
                    status="SKIPPED" if not _is_device_error(exc) else "FAILED",
                    dry_run=dry_run,
                    error_message=exc.message,
                )
            except Exception as exc:
                logger.exception(
                    "Unexpected error registering employees.", extra={"device_id": record.id}
                )
                result = DeviceEnrollmentOutcome(
                    device_id=record.id,
                    device_name=record.name,
                    status="FAILED",
                    dry_run=dry_run,
                    # Type only: an unexpected exception's text can carry paths or
                    # addresses, and this is rendered in the portal.
                    error_message=f"Unexpected error ({type(exc).__name__}). See the log.",
                )
            outcome.devices.append(result)

        # Counts only. Names and codes are never logged.
        logger.info(
            "Employee registration finished.",
            extra={
                "dry_run": dry_run,
                "employees_in_hrms": len(employees),
                "devices": [
                    {
                        "device_id": d.device_id,
                        "status": d.status,
                        "added": len(d.added),
                        "present": d.already_present,
                        "failed": len(d.failed),
                    }
                    for d in outcome.devices
                ],
            },
        )
        return outcome

    def _reconcile_one(
        self, record: Device, employees: list[HrmsEmployee], *, dry_run: bool
    ) -> DeviceEnrollmentOutcome:
        device = self._device_factory(record)
        if not isinstance(device, EnrollableDevice):
            raise BridgeError(f"{record.name} does not support registering employees.")

        result = DeviceEnrollmentOutcome(
            device_id=record.id, device_name=record.name, status="OK", dry_run=dry_run
        )

        device.connect()
        try:
            plan = plan_additions(employees, device.list_user_records())
            result.already_present = plan.already_present
            result.skipped = [FailedUser(s.employee_code, s.reason) for s in plan.skipped]

            written: list[PlannedAdd] = []
            for item in plan.to_add:
                if dry_run:
                    written.append(item)
                    continue
                try:
                    device.add_user(
                        uid=item.uid,
                        user_id=item.employee_code,
                        name=item.name,
                        card=item.card,
                    )
                    written.append(item)
                except BridgeError as exc:
                    result.failed.append(FailedUser(item.employee_code, exc.message))
                except Exception as exc:
                    logger.exception(
                        "Adding a user to the terminal failed.",
                        extra={"device_id": record.id},
                    )
                    result.failed.append(
                        FailedUser(item.employee_code, f"Unexpected error ({type(exc).__name__}).")
                    )

            if not dry_run and written:
                # Trust the terminal, not the absence of an exception: read it back and
                # only report as added what is actually there.
                after = {u.user_id.strip().lower() for u in device.list_user_records()}
                confirmed: list[PlannedAdd] = []
                for item in written:
                    if item.employee_code.lower() in after:
                        confirmed.append(item)
                    else:
                        result.failed.append(
                            FailedUser(
                                item.employee_code,
                                "The terminal accepted the command but the user is not "
                                "there when read back.",
                            )
                        )
                written = confirmed

            result.added = [
                AddedUser(
                    employee_code=item.employee_code,
                    name=item.name,
                    card_set=item.card is not None,
                    warnings=item.warnings,
                )
                for item in written
            ]
        finally:
            device.disconnect()

        if result.failed or result.skipped:
            result.status = "PARTIAL"
        return result


def _is_device_error(exc: BridgeError) -> bool:
    """A terminal that could not be used is FAILED; a refusal before touching it is SKIPPED."""
    return isinstance(exc, DeviceError)
