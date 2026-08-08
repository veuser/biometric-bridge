"""Session store, rate limiter and password policy in isolation."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums import UserRole
from app.core.security import (
    PasswordPolicyError,
    generate_session_token,
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.services.rate_limiter import RateLimiter
from app.services.session_store import SessionStore


class TestPasswords:
    def test_hash_is_verifiable_and_salted(self) -> None:
        first, second = hash_password("correct-horse-battery"), hash_password(
            "correct-horse-battery"
        )
        assert first != second, "identical passwords must not produce identical hashes"
        assert verify_password(first, "correct-horse-battery")

    def test_wrong_password_fails(self) -> None:
        assert not verify_password(hash_password("correct-horse-battery"), "wrong")

    def test_corrupt_hash_is_a_failed_login_not_a_crash(self) -> None:
        """A damaged row must not become an outage — or a bypass."""
        assert verify_password("not-a-valid-argon2-hash", "anything") is False
        assert verify_password("", "anything") is False

    def test_argon2_is_used(self) -> None:
        assert hash_password("correct-horse-battery").startswith("$argon2id$")

    @pytest.mark.parametrize(
        ("password", "fragment"),
        [
            ("short", "12 characters"),
            ("password123", "12 characters"),
            ("passw0rdpassw0rd", "too common") if False else ("aaaaaaaaaaaaaa", "repeated"),
        ],
    )
    def test_weak_passwords_are_rejected(self, password: str, fragment: str) -> None:
        with pytest.raises(PasswordPolicyError, match=fragment):
            validate_password_policy(password)

    def test_common_password_is_rejected(self) -> None:
        with pytest.raises(PasswordPolicyError, match="too common"):
            validate_password_policy("biometricbridge")

    def test_a_long_passphrase_is_accepted(self) -> None:
        validate_password_policy("correct horse battery staple")

    def test_tokens_are_unique_and_long(self) -> None:
        tokens = {generate_session_token() for _ in range(200)}
        assert len(tokens) == 200
        assert all(len(t) >= 40 for t in tokens)


class TestSessionStore:
    def _store(self, **kwargs: timedelta) -> SessionStore:
        return SessionStore(**kwargs)  # type: ignore[arg-type]

    def test_create_then_get(self) -> None:
        store = self._store()
        session = store.create(user_id=1, username="admin", role=UserRole.ADMIN)
        assert store.get(session.token) is not None

    def test_unknown_and_empty_tokens_return_none(self) -> None:
        store = self._store()
        assert store.get("nope") is None
        assert store.get(None) is None

    def test_revoke_is_immediate(self) -> None:
        store = self._store()
        session = store.create(user_id=1, username="admin", role=UserRole.ADMIN)
        store.revoke(session.token)
        assert store.get(session.token) is None

    def test_idle_timeout_expires_a_session(self) -> None:
        store = self._store(idle_timeout=timedelta(seconds=-1))
        session = store.create(user_id=1, username="admin", role=UserRole.ADMIN)
        assert store.get(session.token) is None

    def test_absolute_lifetime_expires_a_session_despite_activity(self) -> None:
        """Bounds the damage from a cookie sniffed off the plain-HTTP LAN."""
        store = self._store(
            idle_timeout=timedelta(hours=8), absolute_lifetime=timedelta(seconds=-1)
        )
        session = store.create(user_id=1, username="admin", role=UserRole.ADMIN)
        assert store.get(session.token) is None

    def test_activity_refreshes_the_idle_clock(self) -> None:
        store = self._store()
        session = store.create(user_id=1, username="admin", role=UserRole.ADMIN)
        first = store.get(session.token)
        second = store.get(session.token)
        assert first is not None and second is not None
        assert second.last_seen_at >= first.last_seen_at

    def test_revoke_all_for_user_leaves_other_users_alone(self) -> None:
        store = self._store()
        mine = store.create(user_id=1, username="a", role=UserRole.ADMIN)
        theirs = store.create(user_id=2, username="b", role=UserRole.ADMIN)
        assert store.revoke_all_for_user(1) == 1
        assert store.get(mine.token) is None
        assert store.get(theirs.token) is not None

    def test_purge_expired_reclaims_memory(self) -> None:
        store = self._store(idle_timeout=timedelta(seconds=-1))
        store.create(user_id=1, username="a", role=UserRole.ADMIN)
        assert store.purge_expired() == 1
        assert store.active_count() == 0


class TestRateLimiter:
    def test_allows_up_to_the_limit(self) -> None:
        limiter = RateLimiter(max_attempts=3)
        for _ in range(3):
            assert limiter.check("k")
            limiter.record_failure("k")
        assert not limiter.check("k")

    def test_success_resets_the_counter(self) -> None:
        limiter = RateLimiter(max_attempts=3)
        limiter.record_failure("k")
        limiter.record_failure("k")
        limiter.reset("k")
        assert limiter.check("k")

    def test_window_expiry_frees_the_key(self) -> None:
        limiter = RateLimiter(max_attempts=1, window=timedelta(seconds=-1))
        limiter.record_failure("k")
        assert limiter.check("k")

    def test_keys_are_independent(self) -> None:
        """Keyed on username+IP so one host cannot lock another out."""
        limiter = RateLimiter(max_attempts=1)
        limiter.record_failure("admin|10.0.0.1")
        assert not limiter.check("admin|10.0.0.1")
        assert limiter.check("admin|10.0.0.2")

    def test_retry_after_is_zero_below_the_limit(self) -> None:
        limiter = RateLimiter(max_attempts=3)
        limiter.record_failure("k")
        assert limiter.retry_after_seconds("k") == 0

    def test_retry_after_is_positive_once_limited(self) -> None:
        limiter = RateLimiter(max_attempts=1, window=timedelta(minutes=15))
        limiter.record_failure("k")
        assert 0 < limiter.retry_after_seconds("k") <= 15 * 60
