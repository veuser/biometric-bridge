"""A scriptable HRMS, served through httpx.MockTransport.

Lets every response shape a real HRMS might produce — 401, 400, 500, 429, timeout,
partial acceptance, empty body — be tested without a network or a real HRMS. It also
records what the bridge actually sent, which is how the payload-shape tests assert
that no unexpected field leaks into someone else's API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeHrms:
    status_code: int = 200
    response_body: Any = field(default_factory=lambda: {"success": True, "processed": 0})
    raise_timeout: bool = False
    raise_connect_error: bool = False
    #: Fail this many times, then succeed. Models a transient outage.
    fail_times: int = 0
    fail_status: int = 503

    requests: list[RecordedRequest] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> RecordedRequest:
        return self.requests[-1]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body: Any
        try:
            body = json.loads(request.content) if request.content else None
        except ValueError:
            body = request.content.decode(errors="replace")

        self.requests.append(
            RecordedRequest(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                body=body,
            )
        )

        if self.raise_timeout:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if self.raise_connect_error:
            raise httpx.ConnectError("simulated connection failure", request=request)

        if self.fail_times > 0:
            self.fail_times -= 1
            return httpx.Response(self.fail_status, json={"error": "temporarily unavailable"})

        if self.response_body is None:
            return httpx.Response(self.status_code)
        return httpx.Response(self.status_code, json=self.response_body)
