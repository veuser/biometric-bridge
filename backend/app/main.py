"""FastAPI application factory and process lifespan.

Startup must never block on something outside this machine's control. A biometric
device that is switched off or an HRMS that is unreachable are *normal* conditions on
a customer LAN — the service still has to start so an operator can log in and see
*why* things are unhealthy (PLAN.md §44). Connectivity is therefore probed
asynchronously by the health monitor, never during startup.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.routes import auth as auth_routes
from app.api.routes import devices as device_routes
from app.api.routes import health as health_routes
from app.api.routes import hrms as hrms_routes
from app.api.routes import sync as sync_routes
from app.api.routes import users as user_routes
from app.core.config import Settings, get_settings
from app.core.errors import BridgeError, SyncInProgressError
from app.core.logging import configure_logging, get_logger
from app.database.database import DatabaseManager
from app.database.migrator import upgrade_to_head
from app.services.auth_service import AuthService
from app.services.health_service import HealthService
from app.services.hrms_service import HrmsService
from app.services.rate_limiter import RateLimiter
from app.services.session_store import SessionStore
from app.services.sync_lock import clear_locks_owned_by_this_process
from app.services.sync_runner import close_interrupted_runs

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers.

    HSTS is deliberately absent: the portal runs over plain HTTP on the LAN
    (PLAN.md §8). Sending HSTS over HTTP is ignored by browsers anyway, and sending
    it once TLS is enabled is handled by flipping ``portal_tls_enabled``.
    """

    def __init__(self, app: object, *, tls_enabled: bool) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._tls_enabled = tls_enabled

    async def dispatch(self, request: Request, call_next: object) -> Response:
        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        if self._tls_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    paths = settings.paths
    paths.ensure()

    configure_logging(
        level=settings.log_level,
        log_file=paths.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        to_stdout=settings.log_to_stdout,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "Biometric Bridge starting.",
            extra={
                "app_env": settings.app_env,
                "data_root": str(paths.root),
                "timezone": settings.default_timezone,
            },
        )
        # Before anything opens a session: an upgraded binary against a
        # last-version database would otherwise 500 on every table it touches.
        upgrade_to_head(settings)

        db = DatabaseManager(settings)
        sessions = SessionStore()
        app.state.settings = settings
        app.state.db = db
        app.state.sessions = sessions
        app.state.auth_service = AuthService(sessions, RateLimiter())
        app.state.health_service = HealthService(db)

        # Redaction state is per-process: without this the stored HRMS key would be
        # unprotected in the log until someone next saved the configuration.
        with db.session() as boot_session:
            HrmsService().register_existing_secret(boot_session)
            # A run left RUNNING by a killed process would otherwise sit in the
            # history forever and make the dashboard claim a sync is in progress.
            close_interrupted_runs(boot_session)
            clear_locks_owned_by_this_process(boot_session)

        logger.info("Startup complete; serving HTTP.", extra={"port": settings.port})
        try:
            yield
        finally:
            db.dispose()
            logger.info("Biometric Bridge stopped.")

    app = FastAPI(
        title="Biometric Bridge",
        version="0.1.0",
        summary="Pushes eSSL/ZK biometric attendance punches to an existing HRMS API.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(SecurityHeadersMiddleware, tls_enabled=settings.portal_tls_enabled)

    app.include_router(health_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(user_routes.router)
    app.include_router(device_routes.router)
    app.include_router(hrms_routes.router)
    app.include_router(sync_routes.router)

    _register_exception_handlers(app)
    _mount_portal(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SyncInProgressError)
    async def _sync_in_progress(_request: Request, exc: SyncInProgressError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.message})

    @app.exception_handler(BridgeError)
    async def _bridge_error(_request: Request, exc: BridgeError) -> JSONResponse:
        # exc.message is written for operators; the stack trace goes to the log only,
        # never to the portal (PLAN.md §19).
        logger.warning("Request failed.", exc_info=exc, extra={"status": exc.status})
        return JSONResponse(
            status_code=400, content={"detail": exc.message, "status": str(exc.status)}
        )


def _mount_portal(app: FastAPI) -> None:
    """Serve the built React app, if present.

    Absent during backend-only development and in the test suite, so this is a
    no-op rather than an error — the API must be usable without a frontend build.
    """
    if not STATIC_DIR.is_dir() or not (STATIC_DIR / "index.html").is_file():
        logger.info("No frontend build found; serving API only.", extra={"path": str(STATIC_DIR)})
        return

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        """Client-side routing: unknown non-API paths return index.html."""
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file() and STATIC_DIR in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")


app = create_app()
