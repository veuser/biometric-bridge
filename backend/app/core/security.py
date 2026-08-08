"""Password hashing, password policy, and session-token generation.

Argon2id is used with the library defaults, which are the current OWASP-aligned
parameters. The hash string embeds its own parameters, so raising the cost later
re-hashes users transparently on their next login via :func:`needs_rehash`.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

#: Default floor. Configurable via BIOMETRIC_BRIDGE_MIN_PASSWORD_LENGTH so a
#: deployment can set its own bar — the portal runs over plain HTTP on a LAN, so
#: this is the only thing between a passer-by and full configuration access.
DEFAULT_MIN_PASSWORD_LENGTH = 4

#: At or above this, the extra checks (common passwords, low character variety)
#: apply. Below it the operator has deliberately lowered the bar, and being then
#: blocked by a blocklist would just be irritating rather than protective.
STRICT_POLICY_THRESHOLD = 12

SESSION_TOKEN_BYTES = 32

#: Rejected outright regardless of length. Not a substitute for a real breach
#: corpus — just the handful that get typed into an appliance on a factory floor.
_BANNED_PASSWORDS = frozenset(
    {
        "password", "passw0rd", "password123", "administrator", "admin@123",
        "biometric", "biometricbridge", "changeme", "letmein", "welcome123",
        "qwerty123456", "123456789012", "iloveyou123",
    }
)


class PasswordPolicyError(ValueError):
    """Raised when a proposed password fails policy. Message is shown to the user."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time-ish verification that never raises on a bad password.

    A malformed stored hash is treated as a failed login rather than a 500 — a
    corrupted row must not become an authentication bypass *or* an outage.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def validate_password_policy(
    password: str,
    *,
    username: str | None = None,
    min_length: int = DEFAULT_MIN_PASSWORD_LENGTH,
) -> None:
    """Raise :class:`PasswordPolicyError` if the password is unacceptable.

    Length is weighted over composition rules deliberately: a long passphrase beats
    ``P@ssw0rd!`` and operators are far more likely to actually use it.

    The blocklist and character-variety checks apply only when ``min_length`` is at
    least :data:`STRICT_POLICY_THRESHOLD`. A deployment that has lowered the minimum
    has made a deliberate choice; second-guessing it with a blocklist would block
    ``admin`` while still allowing ``adm1n``, which is friction without protection.
    """
    if not password:
        raise PasswordPolicyError("Password is required.")

    if len(password) < min_length:
        plural = "" if min_length == 1 else "s"
        raise PasswordPolicyError(
            f"Password must be at least {min_length} character{plural}."
        )

    if username and username.lower() == password.lower():
        # Cheap enough to keep at any policy level, and catches the worst habit.
        raise PasswordPolicyError("Password must not be the same as the username.")

    if min_length < STRICT_POLICY_THRESHOLD:
        return

    if password.lower() in _BANNED_PASSWORDS:
        raise PasswordPolicyError("That password is too common. Choose a different one.")
    if username and username.lower() in password.lower():
        raise PasswordPolicyError("Password must not contain the username.")
    if len(set(password)) < 5:
        raise PasswordPolicyError("Password must not be a small set of repeated characters.")


def generate_session_token() -> str:
    """Opaque, unguessable session identifier.

    The token carries no claims: it is a lookup key into the server-side session
    store, so logout and role changes take effect immediately. That is the reason
    this is not a JWT.
    """
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)
