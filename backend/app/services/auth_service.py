"""Authentication and portal user management."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.core.enums import UserRole
from app.core.logging import get_logger
from app.core.security import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    validate_password_policy,
    verify_password,
)
from app.database.models import User
from app.services.rate_limiter import RateLimiter
from app.services.session_store import Session, SessionStore
from app.utils.datetime_utils import utcnow

logger = get_logger(__name__)

#: Consecutive failures before the account itself is locked. Higher than the rate
#: limit, so ordinary typos hit the throttle (which clears itself) long before they
#: hit the lockout (which needs an admin or a wait).
MAX_FAILED_ATTEMPTS = 10
LOCKOUT_DURATION = timedelta(minutes=30)


class AuthError(Exception):
    """Authentication failure. The message is safe to show the user."""


class RateLimitedError(AuthError):
    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AuthService:
    def __init__(self, sessions: SessionStore, limiter: RateLimiter) -> None:
        self._sessions = sessions
        self._limiter = limiter

    # ── login ────────────────────────────────────────────────────────────────

    def login(
        self, db: DbSession, *, username: str, password: str, client_ip: str
    ) -> Session:
        key = f"{username.lower()}|{client_ip}"

        if not self._limiter.check(key):
            retry_after = self._limiter.retry_after_seconds(key)
            logger.warning(
                "Login throttled.", extra={"username": username, "client_ip": client_ip}
            )
            raise RateLimitedError(
                "Too many failed sign-in attempts. Try again later.", retry_after
            )

        user = db.execute(
            select(User).where(func.lower(User.username) == username.lower())
        ).scalar_one_or_none()

        # Every rejection below returns the same message. Distinguishing "no such
        # user" from "wrong password" would confirm which accounts exist.
        generic = "Incorrect username or password."

        if user is None:
            # Hash anyway so a missing user is not detectably faster than a wrong
            # password. Argon2 is slow enough that the difference would be obvious.
            hash_password(password)
            self._limiter.record_failure(key)
            raise AuthError(generic)

        now = utcnow()
        if user.locked_until and user.locked_until > now:
            logger.warning("Login attempt on a locked account.", extra={"username": username})
            raise AuthError("This account is temporarily locked. Try again later.")

        if not user.is_active:
            self._limiter.record_failure(key)
            raise AuthError(generic)

        if not verify_password(user.password_hash, password):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + LOCKOUT_DURATION
                logger.warning(
                    "Account locked after repeated failures.",
                    extra={"username": username, "failed_attempts": user.failed_attempts},
                )
            db.flush()
            self._limiter.record_failure(key)
            raise AuthError(generic)

        # Success. Transparently upgrade the hash if the cost parameters have moved.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        db.flush()

        self._limiter.reset(key)
        logger.info("Login succeeded.", extra={"username": user.username})
        return self._sessions.create(
            user_id=user.id, username=user.username, role=UserRole(user.role)
        )

    def logout(self, token: str | None) -> None:
        self._sessions.revoke(token)

    # ── user management ──────────────────────────────────────────────────────

    @staticmethod
    def user_count(db: DbSession) -> int:
        return db.execute(select(func.count()).select_from(User)).scalar_one()

    def create_user(
        self,
        db: DbSession,
        *,
        username: str,
        password: str,
        role: UserRole = UserRole.ADMIN,
    ) -> User:
        username = username.strip()
        if not username:
            raise AuthError("Username is required.")

        existing = db.execute(
            select(User).where(func.lower(User.username) == username.lower())
        ).scalar_one_or_none()
        if existing is not None:
            raise AuthError("That username is already taken.")

        try:
            validate_password_policy(password, username=username)
        except PasswordPolicyError as exc:
            raise AuthError(str(exc)) from exc

        user = User(username=username, password_hash=hash_password(password), role=role)
        db.add(user)
        db.flush()
        logger.info("User created.", extra={"username": username, "role": str(role)})
        return user

    def change_password(
        self, db: DbSession, *, user_id: int, new_password: str
    ) -> None:
        user = db.get(User, user_id)
        if user is None:
            raise AuthError("User not found.")
        try:
            validate_password_policy(new_password, username=user.username)
        except PasswordPolicyError as exc:
            raise AuthError(str(exc)) from exc

        user.password_hash = hash_password(new_password)
        user.failed_attempts = 0
        user.locked_until = None
        db.flush()

        # A password change must invalidate sessions opened with the old one.
        revoked = self._sessions.revoke_all_for_user(user_id)
        logger.info(
            "Password changed.", extra={"username": user.username, "sessions_revoked": revoked}
        )

    def set_active(self, db: DbSession, *, user_id: int, is_active: bool) -> User:
        user = db.get(User, user_id)
        if user is None:
            raise AuthError("User not found.")
        user.is_active = is_active
        db.flush()
        if not is_active:
            self._sessions.revoke_all_for_user(user_id)
        return user
