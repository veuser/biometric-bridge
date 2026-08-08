"""Schema migration at startup.

The service brings its own schema up to date when it starts, rather than depending on
an operator having run a CLI step. On a customer machine with no Python tooling, an
upgrade that silently leaves the database one revision behind produces 500s from
every endpoint that touches a table — which is exactly what "install the new version
and restart the service" would otherwise cause.

``script_location`` is resolved relative to this package rather than the working
directory, because under PyInstaller there is no ``backend/`` directory and the
process CWD is wherever the service happened to start.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _migrations_dir() -> Path:
    """Locate the migrations directory in both a checkout and a frozen build.

    PyInstaller unpacks bundled data under ``sys._MEIPASS``; the spec file ships
    ``migrations/`` there. In a source checkout it sits next to ``app/``.
    """
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidate = Path(frozen_root) / "migrations"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[2] / "migrations"


def build_alembic_config(settings: Settings) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_migrations_dir()))
    config.set_main_option("sqlalchemy.url", settings.resolved_database_url())
    # env.py reads this to skip re-resolving settings, so the running service and the
    # migration can never disagree about which file they are touching.
    config.attributes["configured_url"] = settings.resolved_database_url()
    return config


def upgrade_to_head(settings: Settings) -> None:
    """Bring the database to the latest revision. Idempotent and safe to re-run."""
    settings.paths.ensure()
    config = build_alembic_config(settings)
    logger.info("Applying database migrations.", extra={"script_location": str(_migrations_dir())})
    command.upgrade(config, "head")
    logger.info("Database schema is up to date.")
