"""Shared test fixtures.

Every test gets its own data root under ``tmp_path``, so nothing ever touches
``C:\\ProgramData`` and tests cannot leak state into each other through a shared
SQLite file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.logging import clear_secrets
from app.database.database import DatabaseManager
from app.database.models import Base


@pytest.fixture(autouse=True)
def _isolated_secrets() -> Iterator[None]:
    """Registered secrets are process-global; reset around every test."""
    clear_secrets()
    yield
    clear_secrets()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        data_root=str(tmp_path / "bridge"),
        log_to_stdout=False,
        log_level="DEBUG",
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[DatabaseManager]:
    """A database with the schema created from metadata.

    Schema comes from ``create_all`` rather than Alembic for speed; a separate
    integration test asserts the two agree.
    """
    manager = DatabaseManager(settings)
    Base.metadata.create_all(manager.engine)
    yield manager
    manager.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    from app.main import create_app

    app = create_app(settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.db.engine)
        yield test_client
