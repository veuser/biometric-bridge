"""Service entry point.

Separate from ``app/main.py`` because the frozen build needs a plain script with no
package-relative imports, and because the service must never be started by a
reload-enabled dev server.

Uvicorn is run programmatically rather than through its CLI so that a startup
failure is logged to the application log where support can find it, instead of
vanishing into a Windows service that reports only "the service terminated
unexpectedly".
"""

from __future__ import annotations

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # In a frozen build the package sits alongside the executable.
    sys.path.insert(0, str(Path(sys.executable).parent))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    import uvicorn

    from app.core.config import get_settings
    from app.core.logging import get_logger

    settings = get_settings()
    logger = get_logger("service")

    try:
        from app.main import create_app

        application = create_app(settings)
    except Exception:
        # configure_logging runs inside create_app, so this may only reach stderr —
        # which WinSW captures to its own log. Either way it must not be silent.
        import traceback

        traceback.print_exc()
        print("Biometric Bridge failed to start.", file=sys.stderr)
        return 1

    logger.info(
        "Starting HTTP server.",
        extra={"host": settings.host, "port": settings.port, "app_env": settings.app_env},
    )

    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        log_config=None,  # the app has already configured logging
        access_log=False,
        workers=1,  # SQLite plus in-memory sessions and locks: one process only
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
