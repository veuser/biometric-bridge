"""HTTP client for the existing HRMS attendance API.

Knows how to make a request; knows nothing about any specific HRMS. Everything
contract-shaped comes from :class:`HrmsProfile`.

The API key is passed in per call rather than held on the instance, so it lives in a
local for the shortest possible time and is never an attribute someone can
accidentally log by dumping the object.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.biometric.normalizer import BiometricPunch
from app.core.errors import (
    HrmsAuthenticationError,
    HrmsBadRequestError,
    HrmsNotConfiguredError,
    HrmsTimeoutError,
    HrmsUnavailableError,
)
from app.core.logging import get_logger
from app.hrms.mapper import build_request_body
from app.hrms.profile import AuthScheme, HrmsProfile, resolve_path

logger = get_logger(__name__)


@dataclass(slots=True)
class PushResult:
    """Outcome of one batch.

    ``accepted_ids``/``rejected_ids`` are populated only when the profile declares a
    per-record results path. Without it the batch is all-or-nothing, which is the
    common case and needs no extra ceremony.
    """

    status_code: int
    processed: int
    failed: int
    accepted_ids: set[str] = field(default_factory=set)
    rejected_ids: set[str] = field(default_factory=set)
    #: Subset of ``rejected_ids`` that HRMS has refused *finally* — an unenrolled
    #: employee code, a malformed record. Resending cannot change the answer, so the
    #: watermark is allowed past them. A rejection that is NOT in this set is treated
    #: as possibly transient and does hold the watermark back.
    terminal_ids: set[str] = field(default_factory=set)
    error_message: str | None = None
    duration_ms: int = 0

    @property
    def is_partial(self) -> bool:
        return bool(self.rejected_ids) and bool(self.accepted_ids)


class HrmsClient:
    def __init__(
        self,
        *,
        base_url: str,
        profile: HrmsProfile,
        connect_timeout: float = 10.0,
        request_timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not profile.is_configured:
            raise HrmsNotConfiguredError(
                "The HRMS attendance endpoint has not been configured yet. "
                "Set it on the HRMS Configuration page before enabling sync."
            )
        self._base_url = base_url.rstrip("/")
        self._profile = profile
        self._timeout = httpx.Timeout(request_timeout, connect=connect_timeout)
        self._transport = transport

    @property
    def profile(self) -> HrmsProfile:
        """The active contract. The sync engine needs it to size batches."""
        return self._profile

    # ── request construction ────────────────────────────────────────────────

    def _headers(self, api_key: str, tenant: str | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BiometricBridge/0.1",
        }
        headers.update(self._profile.extra_headers)

        auth = self._profile.auth
        if auth.scheme is AuthScheme.HEADER:
            headers[auth.header_name] = auth.value_template.format(api_key=api_key)
        elif auth.scheme is AuthScheme.BEARER:
            headers["Authorization"] = f"Bearer {api_key}"

        if tenant and self._profile.tenant.header_name:
            headers[self._profile.tenant.header_name] = tenant
        return headers

    def _params(self, api_key: str, tenant: str | None) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._profile.auth.scheme is AuthScheme.QUERY:
            params[self._profile.auth.query_param] = api_key
        if tenant and self._profile.tenant.query_param:
            params[self._profile.tenant.query_param] = tenant
        return params

    # ── operations ───────────────────────────────────────────────────────────

    def push(
        self, punches: list[BiometricPunch], *, api_key: str, tenant: str | None = None
    ) -> PushResult:
        """Send one batch. Raises a classified error on failure."""
        body = build_request_body(punches, self._profile)
        url = f"{self._base_url}{self._profile.attendance_path}"
        started = time.monotonic()

        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                response = http.request(
                    self._profile.method,
                    url,
                    json=body,
                    headers=self._headers(api_key, tenant),
                    params=self._params(api_key, tenant) or None,
                )
        except httpx.TimeoutException as exc:
            # Retried, which is only safe if the endpoint is idempotent — the
            # assumption recorded in PLAN.md §5.5.
            raise HrmsTimeoutError("HRMS did not respond within the timeout.") from exc
        except httpx.HTTPError as exc:
            raise HrmsUnavailableError("HRMS could not be reached.") from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        return self._interpret(response, punches, duration_ms)

    def test_connection(self, *, api_key: str, tenant: str | None = None) -> tuple[bool, str]:
        """Probe HRMS without sending attendance data.

        Never fabricates a punch to prove connectivity — that would write invented
        attendance into a production HRMS.
        """
        probe = self._profile.test_connection
        if not probe.path:
            return (
                False,
                "No HRMS test endpoint is configured, so connectivity cannot be "
                "verified without sending real attendance data.",
            )

        url = f"{self._base_url}{probe.path}"
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                response = http.request(
                    probe.method,
                    url,
                    headers=self._headers(api_key, tenant),
                    params=self._params(api_key, tenant) or None,
                )
        except httpx.TimeoutException:
            return False, "HRMS did not respond within the timeout."
        except httpx.HTTPError:
            return False, "HRMS could not be reached. Check the base URL and network."

        if response.status_code in (401, 403):
            return False, "HRMS rejected the API key."
        if response.status_code >= 500:
            return False, f"HRMS returned a server error ({response.status_code})."
        if response.status_code >= 400:
            return False, f"HRMS returned {response.status_code}."
        return True, f"Connected ({response.status_code})."

    # ── response interpretation ──────────────────────────────────────────────

    def _interpret(
        self, response: httpx.Response, punches: list[BiometricPunch], duration_ms: int
    ) -> PushResult:
        code = response.status_code
        config = self._profile.response

        if code in (401, 403):
            raise HrmsAuthenticationError(
                "HRMS rejected the API key. Check the key on the HRMS Configuration page."
            )
        if code == 429 or code >= 500:
            raise HrmsUnavailableError(f"HRMS is unavailable (HTTP {code}).")
        if code >= 400:
            raise HrmsBadRequestError(
                f"HRMS rejected the request (HTTP {code}): {_short_body(response)}"
            )
        if code not in config.success_status_codes:
            raise HrmsBadRequestError(
                f"HRMS returned an unexpected status ({code}) for this profile."
            )

        payload = _safe_json(response)

        # Some APIs return 200 with a failure flag in the body.
        if config.success_path is not None:
            flag = resolve_path(payload, config.success_path)
            if flag is False or (isinstance(flag, str) and flag.lower() in {"false", "error"}):
                message = resolve_path(payload, config.error_message_path) or "no reason given"
                raise HrmsBadRequestError(f"HRMS reported a failure: {message}")

        accepted, rejected, terminal = self._per_record_results(payload, punches)

        processed = _as_int(resolve_path(payload, config.processed_count_path))
        failed = _as_int(resolve_path(payload, config.failed_count_path))
        if processed is None:
            processed = len(accepted) if accepted else len(punches) - len(rejected)
        if failed is None:
            failed = len(rejected)

        logger.info(
            "HRMS batch accepted.",
            extra={
                "status_code": code,
                "sent": len(punches),
                "processed": processed,
                "failed": failed,
                "duration_ms": duration_ms,
            },
        )
        return PushResult(
            status_code=code,
            processed=processed,
            failed=failed,
            accepted_ids=accepted,
            rejected_ids=rejected,
            terminal_ids=terminal,
            error_message=resolve_path(payload, config.error_message_path),
            duration_ms=duration_ms,
        )

    def _per_record_results(
        self, payload: Any, punches: list[BiometricPunch]
    ) -> tuple[set[str], set[str], set[str]]:
        """Read a per-record result array, if the profile declares one.

        This is what makes PARTIAL reachable. Without it the batch is all-or-nothing
        and all three sets come back empty.

        Returns ``(accepted, rejected, terminal)``, where ``terminal`` is the subset
        of ``rejected`` the API has refused for good — see
        ``ResponseConfig.result_terminal_statuses`` for why the distinction decides
        whether the watermark may move.
        """
        config = self._profile.response
        if not config.results_path or not config.result_id_path:
            return set(), set(), set()

        results = resolve_path(payload, config.results_path)
        if not isinstance(results, list):
            return set(), set(), set()

        by_id = {p.client_record_id: p for p in punches}
        # Fall back to employee code when the profile does not send our record id.
        by_code = {p.employee_code: p for p in punches}

        accepted: set[str] = set()
        rejected: set[str] = set()
        terminal: set[str] = set()

        for entry in results:
            identifier = resolve_path(entry, config.result_id_path)
            if identifier is None:
                continue
            punch = by_id.get(str(identifier)) or by_code.get(str(identifier))
            if punch is None:
                continue

            record_id = punch.client_record_id

            if config.result_status_path:
                status = str(resolve_path(entry, config.result_status_path) or "")
                if status in config.result_accepted_statuses:
                    accepted.add(record_id)
                else:
                    rejected.add(record_id)
                    # Unknown statuses are treated as retryable on purpose. A status
                    # this profile has never seen might be transient, and holding the
                    # watermark is the recoverable mistake — advancing past a record
                    # that was never stored loses it silently and permanently.
                    if status in config.result_terminal_statuses:
                        terminal.add(record_id)
                continue

            ok = resolve_path(entry, config.result_ok_path) if config.result_ok_path else True
            (accepted if ok else rejected).add(record_id)

        return accepted, rejected, terminal


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        # An empty or non-JSON body on a success status is not an error; many APIs
        # answer 204 with nothing at all.
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _short_body(response: httpx.Response, limit: int = 200) -> str:
    """A trimmed body for the operator-facing error message.

    Truncated because an HRMS error page can be a full HTML document, and the
    message is rendered in the portal.
    """
    try:
        text = response.text.strip().replace("\n", " ")
    except Exception:
        return "<unreadable response>"
    return text[:limit] + ("…" if len(text) > limit else "")
