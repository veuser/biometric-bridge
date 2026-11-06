"""The bridge's only write to a terminal is "add a user".

``EnrollableDevice`` is the one place a write lives, and it is kept as small as possible.
This test fails if that surface grows, so that a well-meaning "also let us delete / edit /
clear / set the clock" has to be argued for in review rather than slipping in.

It also pins the separation that protects attendance data: the read-only interface the
sync engine uses must not be able to reach any write.
"""

from __future__ import annotations

import inspect

from app.biometric.base import BiometricDevice, EnrollableDevice
from app.biometric.essl_client import EsslBiometricDevice

ALLOWED_WRITES = {"add_user"}

#: Fragments that would mean a destructive or state-changing terminal operation.
FORBIDDEN_FRAGMENTS = (
    "delete",
    "remove",
    "clear",
    "restart",
    "poweroff",
    "set_time",
    "set_user",
    "update_user",
    "enroll_user",
    "save_user_template",
    "write",
    "format",
)


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def test_the_enrollable_interface_exposes_only_add_user_as_a_write() -> None:
    methods = _public_methods(EnrollableDevice)

    assert methods == {"list_user_records", "add_user"}
    assert methods >= ALLOWED_WRITES


def test_the_read_only_interface_has_no_write_of_any_kind() -> None:
    offenders = [
        name
        for name in _public_methods(BiometricDevice)
        for fragment in FORBIDDEN_FRAGMENTS
        if fragment in name
    ] + [name for name in _public_methods(BiometricDevice) if "add_user" in name]

    assert not offenders, f"BiometricDevice must stay read-only. Offenders: {offenders}"


def test_the_ssl_client_adds_no_destructive_public_method() -> None:
    public = _public_methods(EsslBiometricDevice)

    offenders = sorted(
        name for name in public for fragment in FORBIDDEN_FRAGMENTS if fragment in name
    )

    assert not offenders, f"EsslBiometricDevice exposes forbidden operations: {offenders}"


def test_add_user_never_grants_administrator_privilege() -> None:
    """The privilege argument is fixed at 0 in the implementation, not taken from a caller."""
    source = inspect.getsource(EsslBiometricDevice.add_user)

    assert "privilege=0" in source
    assert "privilege" not in inspect.signature(EsslBiometricDevice.add_user).parameters
