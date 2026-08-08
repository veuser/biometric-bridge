"""Server-side session store.

Sessions live in process memory, not in the database and not in the cookie. The
trade-offs, stated so they are a decision rather than an accident:

* **Restarting the service logs everyone out.** Acceptable — this is an admin portal
  with a handful of operators, and a Windows service restart is a rare event.
* **Nothing to clean up.** No session table, no vacuum job, no rows accumulating on a
  box nobody administers.
* **Revocation is immediate.** Logout, disabling a user, or a role change takes effect
  on the very next request, which a self-contained JWT could not offer without a
  denylist that would reintroduce the server-side state anyway.

Sessions expire on an absolute lifetime *and* an idle timeout. The absolute one is
what bounds the damage from a session cookie sniffed off the LAN, which is a live
risk while the portal runs over plain HTTP (PLAN.md §8).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from app.core.enums import UserRole
from app.core.security import generate_session_token
from app.utils.datetime_utils import utcnow

DEFAULT_IDLE_TIMEOUT = timedelta(hours=8)
DEFAULT_ABSOLUTE_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    user_id: int
    username: str
    role: UserRole
    created_at: datetime
    last_seen_at: datetime


class SessionStore:
    def __init__(
        self,
        *,
        idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
        absolute_lifetime: timedelta = DEFAULT_ABSOLUTE_LIFETIME,
    ) -> None:
        self._idle_timeout = idle_timeout
        self._absolute_lifetime = absolute_lifetime
        self._sessions: dict[str, Session] = {}
        # Device reads run in a worker thread pool, so the store is reachable from
        # more than one thread. A plain lock is ample at this request volume.
        self._lock = threading.Lock()

    def create(self, *, user_id: int, username: str, role: UserRole) -> Session:
        now = utcnow()
        session = Session(
            token=generate_session_token(),
            user_id=user_id,
            username=username,
            role=role,
            created_at=now,
            last_seen_at=now,
        )
        with self._lock:
            self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        """Return the session and refresh its idle clock, or ``None`` if invalid."""
        if not token:
            return None
        now = utcnow()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if self._is_expired(session, now):
                del self._sessions[token]
                return None
            refreshed = replace(session, last_seen_at=now)
            self._sessions[token] = refreshed
            return refreshed

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def revoke_all_for_user(self, user_id: int) -> int:
        """Used when a user is disabled, deleted, or has their password changed."""
        with self._lock:
            doomed = [t for t, s in self._sessions.items() if s.user_id == user_id]
            for token in doomed:
                del self._sessions[token]
        return len(doomed)

    def purge_expired(self) -> int:
        now = utcnow()
        with self._lock:
            doomed = [t for t, s in self._sessions.items() if self._is_expired(s, now)]
            for token in doomed:
                del self._sessions[token]
        return len(doomed)

    def active_count(self) -> int:
        return len(self._sessions)

    def _is_expired(self, session: Session, now: datetime) -> bool:
        return (
            now - session.last_seen_at > self._idle_timeout
            or now - session.created_at > self._absolute_lifetime
        )
