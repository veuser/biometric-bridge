"""Health endpoints.

``/health`` is intentionally unauthenticated and intentionally uninformative: it is
polled by the installer and the service watchdog, so it must work before anyone has
logged in, and therefore must leak nothing. Anything diagnostic lives behind
``/health/detailed``, which requires authentication once Phase 2 lands.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import CurrentSession

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@router.get("/health/detailed", summary="Component health for the dashboard")
def health_detailed(request: Request, _session: CurrentSession) -> dict[str, Any]:
    """Authenticated: component detail is reconnaissance value to an attacker."""
    health_service = request.app.state.health_service
    return health_service.detailed()
