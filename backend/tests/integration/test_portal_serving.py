"""The built portal is served by FastAPI on the same origin as the API.

Same origin is what lets the session cookie work with no CORS configuration at all,
so the routing precedence between the SPA fallback and the API matters.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import STATIC_DIR

portal_built = pytest.mark.skipif(
    not (STATIC_DIR / "index.html").is_file(),
    reason="frontend has not been built into app/static",
)


@portal_built
def test_root_serves_the_portal(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'id="root"' in response.text


@portal_built
def test_client_side_routes_fall_back_to_index(client: TestClient) -> None:
    """React Router owns /devices; a hard refresh there must not 404."""
    assert 'id="root"' in client.get("/devices").text


@portal_built
def test_api_routes_are_not_shadowed_by_the_spa_fallback(client: TestClient) -> None:
    """The catch-all is registered last; if that ever changed, every API call
    would start returning the HTML shell with status 200."""
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/openapi.json").status_code == 200


@portal_built
def test_assets_are_served(client: TestClient) -> None:
    assets = list((STATIC_DIR / "assets").glob("*.js"))
    assert assets, "the build should emit at least one JS asset"
    response = client.get(f"/assets/{assets[0].name}")
    assert response.status_code == 200


@portal_built
def test_portal_loads_nothing_from_a_cdn(client: TestClient) -> None:
    """These machines only have outbound HTTPS to HRMS; an external reference
    would leave the portal broken on site (PLAN.md §32)."""
    html = client.get("/").text
    assert "http://" not in html
    assert "https://" not in html


def test_api_works_without_a_frontend_build(client: TestClient) -> None:
    """Backend-only development and CI must not depend on npm."""
    assert client.get("/health").status_code == 200
