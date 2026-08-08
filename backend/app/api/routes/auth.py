"""Authentication endpoints.

The session token is set as an httpOnly cookie and never appears in a response body,
so portal JavaScript has no way to read it — which also means an XSS bug in the SPA
cannot exfiltrate a session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import (
    SESSION_COOKIE_NAME,
    CurrentSession,
    Db,
    get_auth_service,
    get_client_ip,
)
from app.core.config import Settings
from app.schemas.auth import (
    BootstrapRequest,
    BootstrapStatus,
    CurrentUser,
    LoginRequest,
)
from app.services.auth_service import AuthError, AuthService, RateLimitedError

router = APIRouter(prefix="/api/auth", tags=["auth"])

AuthDep = Annotated[AuthService, Depends(get_auth_service)]
ClientIp = Annotated[str, Depends(get_client_ip)]


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        # False while the portal runs over plain HTTP on the LAN; flipping
        # portal_tls_enabled is all that is needed once a certificate exists.
        secure=settings.portal_tls_enabled,
        path="/",
    )


@router.get("/bootstrap", response_model=BootstrapStatus)
def bootstrap_status(db: Db, auth: AuthDep) -> BootstrapStatus:
    """Whether the first admin still needs creating.

    The portal calls this before showing a login form so a fresh install sends the
    operator to account creation instead of a form no credential can satisfy.
    """
    return BootstrapStatus(bootstrap_required=auth.user_count(db) == 0)


@router.post("/bootstrap", response_model=CurrentUser, status_code=status.HTTP_201_CREATED)
def bootstrap(
    payload: BootstrapRequest, request: Request, response: Response, db: Db, auth: AuthDep
) -> CurrentUser:
    """Create the first administrator.

    Only ever available while the users table is empty. This is what allows the
    product to ship with **no default credentials** — the single most common way
    appliances like this get compromised.
    """
    if auth.user_count(db) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup has already been completed.",
        )
    try:
        user = auth.create_user(db, username=payload.username, password=payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    session = request.app.state.sessions.create(
        user_id=user.id, username=user.username, role=user.role
    )
    _set_session_cookie(response, session.token, request.app.state.settings)
    return CurrentUser(username=user.username, role=user.role)


@router.post("/login", response_model=CurrentUser)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Db,
    auth: AuthDep,
    client_ip: ClientIp,
) -> CurrentUser:
    try:
        session = auth.login(
            db, username=payload.username, password=payload.password, client_ip=client_ip
        )
    except RateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    _set_session_cookie(response, session.token, request.app.state.settings)
    return CurrentUser(username=session.username, role=session.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def logout(request: Request, response: Response, auth: AuthDep) -> None:
    """Revoke server-side and clear the cookie.

    ``response_model=None`` is required: with ``from __future__ import annotations``
    FastAPI would otherwise infer a body model from the return annotation, which a
    204 is not allowed to have.
    """
    auth.logout(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=CurrentUser)
def me(session: CurrentSession) -> CurrentUser:
    return CurrentUser(username=session.username, role=session.role)
