"""Backups.

A file copy under WAL can capture a mid-transaction database or miss committed data
still in the -wal file, producing a backup that only reveals itself as broken when
someone needs it. These use SQLite's online backup API instead.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.backup_service import BackupService


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "backupadmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


def test_backup_is_a_readable_database(admin: TestClient) -> None:
    created = admin.post("/api/backups")
    assert created.status_code == 201

    settings: Settings = admin.app.state.settings
    path = settings.paths.backups_dir / created.json()["name"]

    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute(
            "select name from sqlite_master where type='table'"
        )}
    finally:
        connection.close()

    assert {"devices", "users", "sync_states"} <= tables


def test_backup_captures_committed_data(admin: TestClient) -> None:
    admin.post("/api/devices", json={"name": "A", "ip_address": "10.0.0.1", "port": 4370})
    created = admin.post("/api/backups").json()

    settings: Settings = admin.app.state.settings
    connection = sqlite3.connect(settings.paths.backups_dir / created["name"])
    try:
        assert connection.execute("select count(*) from devices").fetchone()[0] == 1
    finally:
        connection.close()


def test_backups_taken_in_the_same_second_do_not_overwrite(admin: TestClient) -> None:
    """Filenames carry millisecond precision for exactly this reason."""
    service = BackupService(admin.app.state.settings)
    for _ in range(3):
        service.create()
    assert len(service.list_backups()) == 3


def test_backups_are_listed_newest_first(admin: TestClient) -> None:
    service = BackupService(admin.app.state.settings)
    for _ in range(3):
        service.create()
    names = [p.name for p in service.list_backups()]
    assert names == sorted(names, reverse=True)


def test_retention_bounds_the_backup_directory(admin: TestClient) -> None:
    """Unbounded backups eventually fill the disk the live database sits on."""
    service = BackupService(admin.app.state.settings)
    for _ in range(5):
        service.create(keep=2)
    assert len(service.list_backups()) <= 2


def test_backups_require_an_admin_session(client: TestClient) -> None:
    assert client.get("/api/backups").status_code == 401
    assert client.post("/api/backups").status_code == 401
