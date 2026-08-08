"""Authentication end-to-end.

The portal runs over plain HTTP on a LAN (PLAN.md §8), so these controls are doing
real work: no default credentials, no user enumeration, no unauthenticated access to
anything but the liveness probe.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import SESSION_COOKIE_NAME

GOOD_PASSWORD = "correct-horse-battery-staple"
SHORT_PASSWORD = "abcd"
USERNAME = "bridgeadmin"


def _bootstrap(client: TestClient, password: str = GOOD_PASSWORD) -> None:
    response = client.post(
        "/api/auth/bootstrap", json={"username": USERNAME, "password": password}
    )
    assert response.status_code == 201, response.text


def test_fresh_install_reports_bootstrap_required(client: TestClient) -> None:
    assert client.get("/api/auth/bootstrap").json() == {"bootstrap_required": True}


def test_no_default_credentials_exist(client: TestClient) -> None:
    """The most common way an appliance like this gets owned."""
    for username, password in [
        ("admin", "admin"),
        ("admin", "password"),
        ("administrator", "biometric"),
    ]:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 401


def test_bootstrap_creates_admin_and_signs_them_in(client: TestClient) -> None:
    _bootstrap(client)
    assert client.get("/api/auth/bootstrap").json() == {"bootstrap_required": False}
    assert client.get("/api/auth/me").json() == {"username": USERNAME, "role": "ADMIN"}


def test_bootstrap_is_refused_once_a_user_exists(client: TestClient) -> None:
    """Otherwise anyone on the LAN could mint themselves an admin account."""
    _bootstrap(client)
    response = client.post(
        "/api/auth/bootstrap", json={"username": "sneaky", "password": GOOD_PASSWORD}
    )
    assert response.status_code == 409


def test_bootstrap_accepts_a_short_password_by_default(client: TestClient) -> None:
    """The minimum is configurable and ships permissive; raise
    BIOMETRIC_BRIDGE_MIN_PASSWORD_LENGTH to tighten it."""
    response = client.post(
        "/api/auth/bootstrap", json={"username": USERNAME, "password": "abcd"}
    )
    assert response.status_code == 201


def test_bootstrap_rejects_an_empty_password(client: TestClient) -> None:
    response = client.post("/api/auth/bootstrap", json={"username": USERNAME, "password": ""})
    assert response.status_code == 422


def test_bootstrap_enforces_a_raised_minimum(tmp_path: object) -> None:
    """A deployment that raises the bar gets it enforced."""
    from fastapi.testclient import TestClient as Client

    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        app_env="test",
        data_root=str(tmp_path / "strict"),  # type: ignore[operator]
        log_to_stdout=False,
        min_password_length=12,
    )
    with Client(create_app(settings)) as strict:
        response = strict.post(
            "/api/auth/bootstrap", json={"username": USERNAME, "password": "short"}
        )
        assert response.status_code == 400
        assert "12 characters" in response.json()["detail"]


def test_session_cookie_is_httponly_and_not_in_the_body(client: TestClient) -> None:
    """The token must be unreadable to page JavaScript, so XSS cannot steal it."""
    _bootstrap(client)
    response = client.post(
        "/api/auth/login", json={"username": USERNAME, "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie.replace("samesite", "SameSite")
    # Plain HTTP on the LAN, so Secure must be absent or the cookie is never sent.
    assert "Secure" not in set_cookie

    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token and token not in response.text


def test_wrong_password_and_unknown_user_are_indistinguishable(client: TestClient) -> None:
    """Differing messages would confirm which accounts exist."""
    _bootstrap(client)
    wrong_password = client.post(
        "/api/auth/login", json={"username": USERNAME, "password": "wrong-password-here"}
    )
    unknown_user = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "wrong-password-here"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


def test_logout_revokes_the_session_immediately(client: TestClient) -> None:
    _bootstrap(client)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_a_forged_token_is_rejected(client: TestClient) -> None:
    _bootstrap(client)
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
    assert client.get("/api/auth/me").status_code == 401


def test_login_is_throttled_after_repeated_failures(client: TestClient) -> None:
    _bootstrap(client)
    for _ in range(5):
        client.post("/api/auth/login", json={"username": USERNAME, "password": "nope-nope-nope"})

    response = client.post(
        "/api/auth/login", json={"username": USERNAME, "password": "nope-nope-nope"}
    )
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_throttle_does_not_lock_out_a_correct_password_first(client: TestClient) -> None:
    """Only failures consume budget; a valid login must not be penalised."""
    _bootstrap(client)
    for _ in range(4):
        client.post("/api/auth/login", json={"username": USERNAME, "password": "nope-nope-nope"})
    assert (
        client.post(
            "/api/auth/login", json={"username": USERNAME, "password": GOOD_PASSWORD}
        ).status_code
        == 200
    )


class TestProtectedRoutes:
    def test_detailed_health_requires_a_session(self, client: TestClient) -> None:
        assert client.get("/health/detailed").status_code == 401

    def test_liveness_probe_stays_anonymous(self, client: TestClient) -> None:
        """The installer and the service watchdog poll it before anyone logs in."""
        assert client.get("/health").status_code == 200

    def test_user_management_requires_a_session(self, client: TestClient) -> None:
        assert client.get("/api/users").status_code == 401


def test_user_list_never_exposes_password_hashes(client: TestClient) -> None:
    _bootstrap(client)
    body = client.get("/api/users").json()
    assert body[0]["username"] == USERNAME
    assert "password_hash" not in body[0]
    assert "password" not in str(body)


def test_changing_a_password_revokes_existing_sessions(client: TestClient) -> None:
    """A stolen session must not outlive the credential it was opened with."""
    _bootstrap(client)
    user_id = client.get("/api/users").json()[0]["id"]

    response = client.put(
        f"/api/users/{user_id}/password", json={"new_password": "another-long-passphrase"}
    )
    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_an_admin_cannot_deactivate_themselves(client: TestClient) -> None:
    """Undoing that would need database surgery on a machine with no tooling."""
    _bootstrap(client)
    user_id = client.get("/api/users").json()[0]["id"]
    response = client.put(f"/api/users/{user_id}/active", params={"is_active": False})
    assert response.status_code == 400


def test_password_must_not_equal_the_username(client: TestClient) -> None:
    """Enforced at every policy level."""
    response = client.post(
        "/api/auth/bootstrap", json={"username": "bridgeadmin", "password": "bridgeadmin"}
    )
    assert response.status_code == 400
    assert "same as the username" in response.json()["detail"]
