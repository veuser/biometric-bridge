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

MIN_PASSWORD_LENGTH = 12
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


def validate_password_policy(password: str, *, username: str | None = None) -> None:
    """Raise :class:`PasswordPolicyError` if the password is unacceptable.

    Length is weighted over composition rules deliberately: a 12-character
    passphrase beats ``P@ssw0rd!`` and operators are far more likely to actually
    use it.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
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
