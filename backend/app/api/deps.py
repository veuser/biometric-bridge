"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.core.enums import UserRole
from app.services.auth_service import AuthService
from app.services.session_store import Session, SessionStore

SESSION_COOKIE_NAME = "bb_session"


def get_db(request: Request) -> Iterator[DbSession]:
    with request.app.state.db.session() as session:
        yield session


def get_session_store(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.sessions
    return store


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


def get_client_ip(request: Request) -> str:
    """Peer address only.

    ``X-Forwarded-For`` is deliberately ignored: the bridge is reached directly on
    the LAN, so an attacker-supplied header would let anyone forge the key that the
    login throttle counts against.
    """
    return request.client.host if request.client else "unknown"


def current_session(
    request: Request,
    sessions: Annotated[SessionStore, Depends(get_session_store)],
) -> Session:
    """Require an authenticated session, or 401."""
    session = sessions.get(request.cookies.get(SESSION_COOKIE_NAME))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    return session


def require_admin(session: Annotated[Session, Depends(current_session)]) -> Session:
    if session.role is not UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires an administrator account.",
        )
    return session


CurrentSession = Annotated[Session, Depends(current_session)]
AdminSession = Annotated[Session, Depends(require_admin)]
Db = Annotated[DbSession, Depends(get_db)]
