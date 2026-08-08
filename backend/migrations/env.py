"""Alembic environment.

The database URL comes from application settings rather than ``alembic.ini`` so the
CLI and the running service can never disagree about which file they are touching.

``render_as_batch`` is required: SQLite cannot ALTER most column properties, so
Alembic has to rebuild the table. Without it, later migrations that alter a column
fail on SQLite.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the `app` package importable when Alembic runs from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# When the running service migrates itself at startup it passes its own resolved URL
# through config.attributes, so the two can never disagree about which file they are
# touching. The CLI path (alembic revision/upgrade by hand) has no such attribute and
# falls back to resolving settings here.
_configured_url = config.attributes.get("configured_url")
if _configured_url:
    config.set_main_option("sqlalchemy.url", _configured_url)
else:
    _settings = get_settings()
    # Alembic bypasses DatabaseManager, so nothing has created the data directory
    # yet, and SQLite will not create a missing parent directory.
    _settings.paths.ensure()
    config.set_main_option("sqlalchemy.url", _settings.resolved_database_url())


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render ``UtcDateTime`` as the plain SQLAlchemy type it compiles to.

    Autogenerate would otherwise emit ``app.database.models.UtcDateTime(...)``, which
    the migration cannot resolve without importing application code. Migrations must
    stay self-contained: a migration written today has to keep running years from now
    even if ``UtcDateTime`` is renamed or deleted. The DDL is identical either way —
    the decorator only adds Python-side validation.
    """
    from app.database.models import UtcDateTime

    if type_ == "type" and isinstance(obj, UtcDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
