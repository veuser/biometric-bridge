"""Decoding the two integer fields in a ZK attendance record.

**This mapping must be confirmed against the customer's actual hardware before
go-live** (PLAN.md §1.1, Phase 3 action item). eSSL ships many firmware variants and
the encoding is not guaranteed. Everything here therefore degrades to ``UNKNOWN``
rather than guessing, and the raw integers are carried through to the portal so a
mismatch is diagnosable instead of invisible.

The reference spike had these two fields swapped — it read the punch-state field and
labelled it a verification method, which is why a check-out showed up as "Password".
The record layout (``pyzk/base.py``, ``unpack('<H24sB4sB8s', ...)``) is:

    uid, user_id, status, timestamp, punch, space

so ``status`` is the verification mode and ``punch`` is the attendance state.
"""

from __future__ import annotations

from app.core.enums import DeviceRole, PunchType, VerificationType

#: Verification mode — how the person identified themselves.
#:
#: Confirmed against the live terminals on 2026-08-08 (see PLAN.md §1.1): status=1
#: appears alongside varying punch states on both devices, consistent with
#: FINGERPRINT.
#:
#: ``status=4`` is deliberately absent. It is 81% of observed punches on these two
#: terminals, but its meaning is not consistent across ZK firmware families — it is
#: documented variously as face, card, and fingerprint+card. Guessing would put a
#: wrong verification method into someone else's HRMS; UNKNOWN is honest and costs
#: nothing unless the HRMS contract actually consumes this field. Open question 7.
VERIFICATION_BY_STATUS: dict[int, VerificationType] = {
    0: VerificationType.PASSWORD,
    1: VerificationType.FINGERPRINT,
    2: VerificationType.CARD,
    15: VerificationType.FACE,
}

#: Attendance state — what the punch means.
#:
#: Confirmed on live hardware 2026-08-08: the entry terminal reports punch=0 for
#: every record and the exit terminal punch=1 for 85 of 88, which is exactly what
#: this table predicts and is not what the reference spike assumed.
PUNCH_TYPE_BY_PUNCH: dict[int, PunchType] = {
    0: PunchType.CHECK_IN,
    1: PunchType.CHECK_OUT,
    2: PunchType.BREAK_OUT,
    3: PunchType.BREAK_IN,
    4: PunchType.OT_IN,
    5: PunchType.OT_OUT,
}


def decode_verification(status: int) -> VerificationType:
    return VERIFICATION_BY_STATUS.get(status, VerificationType.UNKNOWN)


def decode_punch_type(punch: int, role: DeviceRole) -> PunchType:
    """Resolve the punch type, honouring the device's configured role.

    The reference deployment has a dedicated entry terminal and a dedicated exit
    terminal, so *which device* reported the punch is authoritative and the record's
    own state field is ignored. ``FROM_DEVICE`` defers to the record instead, for
    mixed-mode terminals.
    """
    if role is DeviceRole.CHECK_IN:
        return PunchType.CHECK_IN
    if role is DeviceRole.CHECK_OUT:
        return PunchType.CHECK_OUT
    return PUNCH_TYPE_BY_PUNCH.get(punch, PunchType.UNKNOWN)


def describe_raw(status: int, punch: int) -> str:
    """Human-readable diagnostic for the device page.

    Shown next to the decoded values so an operator comparing the portal against the
    terminal's own report can spot a firmware whose encoding differs.
    """
    verification = VERIFICATION_BY_STATUS.get(status)
    punch_type = PUNCH_TYPE_BY_PUNCH.get(punch)
    return (
        f"status={status} ({verification.value if verification else 'unrecognised'}), "
        f"punch={punch} ({punch_type.value if punch_type else 'unrecognised'})"
    )
