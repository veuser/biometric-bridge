"""Reading the employee list from HRMS."""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import (
    HrmsAuthenticationError,
    HrmsBadRequestError,
    HrmsNotConfiguredError,
    HrmsUnavailableError,
)
from app.hrms.client import HrmsClient
from tests.unit.test_hrms_mapper import configured

API_KEY = "hrms-live-key-abc123456"


def client_with(handler, **profile_overrides) -> HrmsClient:  # type: ignore[no-untyped-def]
    return HrmsClient(
        base_url="https://hrms.example.com",
        profile=configured(**profile_overrides),
        transport=httpx.MockTransport(handler),
    )


def row(code: str, name: str = "N", card: str | None = None) -> dict[str, object]:
    return {"employeeCode": code, "name": name, "cardNumber": card}


#: Byte-for-byte what HRMS sends. The same literal is pinned on the HRMS side in
#: BiometricEmployeeContractTests.WirePage, so renaming a property on either side fails a
#: test instead of producing a list that parses as empty ("nobody to register") in production.
HRMS_WIRE_PAGE = (
    '{"employees":[{"employeeCode":"IQ-1004","name":"Manoj Settebathula","cardNumber":"12345678"},'
    '{"employeeCode":"IQ-1005","name":"No Card","cardNumber":null}],"nextAfterId":null}'
)


def test_the_exact_payload_hrms_sends_is_read_correctly() -> None:
    from app.services.hrms_service import load_shipped_profile

    client = HrmsClient(
        base_url="https://hrms.example.com",
        profile=load_shipped_profile(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=HRMS_WIRE_PAGE, headers={"content-type": "application/json"}
            )
        ),
    )

    employees = client.list_employees(api_key=API_KEY)

    assert [(e.employee_code, e.name, e.card_number) for e in employees] == [
        ("IQ-1004", "Manoj Settebathula", "12345678"),
        ("IQ-1005", "No Card", None),
    ]


def test_follows_the_cursor_across_pages_and_sends_the_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        after = int(request.url.params["afterId"])
        if after == 0:
            return httpx.Response(200, json={"employees": [row("A"), row("B")], "nextAfterId": 2})
        return httpx.Response(200, json={"employees": [row("C", card="77")], "nextAfterId": None})

    employees = client_with(handler).list_employees(api_key=API_KEY, page_size=2)

    assert [e.employee_code for e in employees] == ["A", "B", "C"]
    assert employees[2].card_number == "77"
    assert all(r.method == "GET" for r in seen)
    assert all(r.url.path == "/api/v1/biometric/employees" for r in seen)
    assert all(r.headers["X-API-Key"] == API_KEY for r in seen)
    assert seen[0].url.params["limit"] == "2"


def test_the_shipped_vectoredge_profile_authenticates_with_the_api_key_header() -> None:
    """HRMS reads the `api_key` header and ignores everything else."""
    from app.services.hrms_service import load_shipped_profile

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"employees": []})

    client = HrmsClient(
        base_url="https://hrms.example.com",
        profile=load_shipped_profile(),
        transport=httpx.MockTransport(handler),
    )
    client.list_employees(api_key=API_KEY)

    assert seen[0].headers["api_key"] == API_KEY
    assert seen[0].url.path == "/api/v1/biometric/employees"


def test_rows_without_a_code_are_ignored_and_a_blank_card_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"employees": [row(""), {"name": "no code"}, row("A", card="  ")]}
        return httpx.Response(200, json=body)

    employees = client_with(handler).list_employees(api_key=API_KEY)

    assert [e.employee_code for e in employees] == ["A"]
    assert employees[0].card_number is None


def test_a_missing_scope_is_reported_as_an_authentication_problem() -> None:
    client = client_with(lambda r: httpx.Response(403))

    with pytest.raises(HrmsAuthenticationError, match="biometric.read"):
        client.list_employees(api_key=API_KEY)


def test_a_server_error_is_unavailable_not_an_empty_list() -> None:
    """An empty list on failure would read as 'nobody to register'."""
    client = client_with(lambda r: httpx.Response(500))

    with pytest.raises(HrmsUnavailableError):
        client.list_employees(api_key=API_KEY)


def test_a_failure_on_a_later_page_does_not_return_a_partial_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["afterId"] == "0":
            return httpx.Response(200, json={"employees": [row("A")], "nextAfterId": 1})
        return httpx.Response(503)

    with pytest.raises(HrmsUnavailableError):
        client_with(handler).list_employees(api_key=API_KEY)


def test_an_unexpected_body_shape_is_rejected() -> None:
    client = client_with(lambda r: httpx.Response(200, json={"something": "else"}))

    with pytest.raises(HrmsBadRequestError, match="unexpected shape"):
        client.list_employees(api_key=API_KEY)


def test_a_cursor_that_does_not_advance_cannot_loop_forever() -> None:
    client = client_with(
        lambda r: httpx.Response(200, json={"employees": [row("A")], "nextAfterId": 0})
    )

    with pytest.raises(HrmsBadRequestError, match="does not advance"):
        client.list_employees(api_key=API_KEY)


def test_a_profile_without_an_employees_path_refuses() -> None:
    client = client_with(lambda r: httpx.Response(200, json={"employees": []}), employees_path=None)

    with pytest.raises(HrmsNotConfiguredError, match="employees_path"):
        client.list_employees(api_key=API_KEY)
