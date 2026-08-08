"""Application boot, health endpoints and the WAL/migration guarantees."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.database.database import DatabaseManager
from app.database.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_health_is_anonymous_and_leaks_nothing(client: TestClient) -> None:
    """Polled by the installer and the service watchdog before anyone logs in."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def _sign_in(client: TestClient) -> None:
    """Detailed health is authenticated, so these tests need a session."""
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "healthadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text


def test_detailed_health_reports_components(client: TestClient) -> None:
    _sign_in(client)
    body = client.get("/health/detailed").json()
    assert body["status"] == "healthy"
    assert body["components"]["database"]["healthy"] is True
    assert body["checked_at"].endswith("+00:00")


def test_detailed_health_reports_unhealthy_instead_of_raising(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A health check that throws is useless exactly when it is needed.

    The failure is injected at the session boundary rather than by breaking the
    engine, so teardown still disposes a real engine.
    """

    def _explode() -> Any:
        raise OperationalError("SELECT 1", {}, Exception(r"C:\ProgramData\secret-path"))

    _sign_in(client)
    monkeypatch.setattr(client.app.state.db, "session", _explode)

    body = client.get("/health/detailed").json()
    assert body["status"] == "unhealthy"
    assert body["components"]["database"]["healthy"] is False
    # Only the exception type — never a path or a connection string.
    assert body["components"]["database"]["detail"] == "OperationalError"
    assert "ProgramData" not in str(body)


def test_security_headers_are_present(client: TestClient) -> None:
    headers = client.get("/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_no_hsts_while_the_portal_is_plain_http(client: TestClient) -> None:
    """PLAN.md §8 — TLS is off, so HSTS must not be advertised."""
    assert "Strict-Transport-Security" not in client.get("/health").headers


def test_hsts_appears_once_tls_is_enabled(tmp_path: Path) -> None:
    """The TLS decision is one flag, not a code change."""
    from app.main import create_app

    settings = Settings(
        app_env="test",
        data_root=str(tmp_path / "bridge"),
        log_to_stdout=False,
        portal_tls_enabled=True,
    )
    with TestClient(create_app(settings)) as client:
        assert "Strict-Transport-Security" in client.get("/health").headers


def test_app_starts_without_a_frontend_build(client: TestClient) -> None:
    """The API has to be usable before the portal is built (Phase 7)."""
    assert client.get("/health").status_code == 200


def test_wal_mode_is_active(settings: Settings) -> None:
    """The scheduler writes while the portal reads; without WAL they block."""
    db = DatabaseManager(settings)
    try:
        with db.session() as session:
            assert session.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
    finally:
        db.dispose()


def test_foreign_keys_are_enforced(settings: Settings) -> None:
    """SQLite ignores foreign keys unless asked, per connection."""
    db = DatabaseManager(settings)
    try:
        with db.session() as session:
            assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        db.dispose()


def test_alembic_migration_matches_the_models(tmp_path: Path) -> None:
    """Guards the gap between ``create_all`` (used by tests) and Alembic (used in
    production). If they drift, a fresh install gets a different schema than the
    one every test ran against."""
    data_root = tmp_path / "migrated"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={
            "BIOMETRIC_BRIDGE_DATA_ROOT": str(data_root),
            "PATH": "",
            "SYSTEMROOT": "C:\\Windows",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    migrated = DatabaseManager(Settings(data_root=str(data_root), log_to_stdout=False))
    from_models = DatabaseManager(
        Settings(data_root=str(tmp_path / "models"), log_to_stdout=False)
    )
    try:
        Base.metadata.create_all(from_models.engine)

        migrated_tables = {
            name for name in inspect(migrated.engine).get_table_names()
            if name != "alembic_version"
        }
        model_tables = set(inspect(from_models.engine).get_table_names())
        assert migrated_tables == model_tables

        for table in sorted(model_tables):
            migrated_cols = {c["name"] for c in inspect(migrated.engine).get_columns(table)}
            model_cols = {c["name"] for c in inspect(from_models.engine).get_columns(table)}
            assert migrated_cols == model_cols, f"column drift in {table}"
    finally:
        migrated.dispose()
        from_models.dispose()


def test_startup_migrates_a_brand_new_database(tmp_path: Path) -> None:
    """Regression: the app must build its own schema on boot.

    Without this, an upgraded binary pointed at a not-yet-migrated database
    returns 500 from every endpoint that touches a table — which is exactly what
    "install the new version and restart the service" would produce.
    """
    from app.main import create_app

    settings = Settings(
        app_env="test", data_root=str(tmp_path / "fresh"), log_to_stdout=False
    )
    assert not settings.paths.database_file.exists()

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/auth/bootstrap").json() == {"bootstrap_required": True}
        assert settings.paths.database_file.exists()


def test_startup_migration_is_idempotent(tmp_path: Path) -> None:
    """The service restarts on every Windows boot; re-running must be a no-op."""
    from app.main import create_app

    settings = Settings(
        app_env="test", data_root=str(tmp_path / "twice"), log_to_stdout=False
    )
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").status_code == 200
