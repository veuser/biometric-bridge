"""The employee-registration endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/bootstrap",
        json={"username": "enrolladmin", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 201, response.text
    return client


def test_requires_a_signed_in_administrator(client: TestClient) -> None:
    assert client.post("/api/enrollment/sync").status_code == 401


def test_the_safe_call_is_the_default(admin: TestClient) -> None:
    """Omitting the flag must preview, never write: a terminal is only written to when the
    caller says dry_run=false in so many words."""
    spec = admin.get("/api/openapi.json").json()
    parameters = spec["paths"]["/api/enrollment/sync"]["post"]["parameters"]

    dry_run = next(p for p in parameters if p["name"] == "dry_run")
    assert dry_run["schema"]["default"] is True


def test_an_unconfigured_hrms_is_a_clear_error_not_an_empty_success(admin: TestClient) -> None:
    """No base URL and no key: an empty result here would read as 'everyone is registered'."""
    response = admin.post("/api/enrollment/sync")

    assert response.status_code == 400
    assert "HRMS" in response.json()["detail"]
