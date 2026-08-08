"""The HRMS contract, expressed as configuration.

This is how "do not invent the HRMS API" is honoured mechanically rather than by
good intentions. The client below knows how to make an HTTP request; it knows
nothing about any particular HRMS. Endpoint, method, auth scheme, field names,
batching shape, datetime format and response parsing all come from a profile stored
in the database.

Consequences worth stating:

* Wiring a real contract is a configuration change, not a code change.
* A field mapped to ``None`` is **omitted entirely** from the payload, so the bridge
  never sends something the customer's API did not ask for — employee names, device
  serials, or a client record id it invented.
* The shipped default is a template with a placeholder path, and the bridge refuses
  to sync until a real ``attendance_path`` is configured. It will not guess.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLACEHOLDER_PATH = "/REPLACE-WITH-YOUR-HRMS-ATTENDANCE-PATH"


class AuthScheme(StrEnum):
    HEADER = "header"
    BEARER = "bearer"
    QUERY = "query"
    NONE = "none"


class BatchMode(StrEnum):
    #: Body is a bare JSON array of records.
    ARRAY = "array"
    #: Body is an object with the array nested under ``envelope_path``.
    ENVELOPE = "envelope"
    #: One HTTP request per record.
    SINGLE = "single"


class DateTimeFormat(StrEnum):
    ISO8601_OFFSET = "iso8601_offset"  # 2026-08-08T12:07:09+05:30
    ISO8601_UTC_Z = "iso8601_utc_z"  # 2026-08-08T06:37:09Z
    #: Anything else: the value is used as a strftime pattern.
    CUSTOM = "custom"


class AuthConfig(BaseModel):
    scheme: AuthScheme = AuthScheme.HEADER
    header_name: str = "X-API-Key"
    #: ``{api_key}`` is substituted at request time. Lets a profile express
    #: "Bearer {api_key}", "ApiKey {api_key}", or a bare key without code changes.
    value_template: str = "{api_key}"
    query_param: str = "api_key"


class TenantConfig(BaseModel):
    header_name: str | None = None
    query_param: str | None = None


class BatchConfig(BaseModel):
    mode: BatchMode = BatchMode.ARRAY
    envelope_path: str = "records"
    size: int = Field(default=200, ge=1, le=5000)


class FieldMap(BaseModel):
    """Names this HRMS uses for each normalized field.

    ``None`` means "this API does not want that field" and the key is left out of
    the payload entirely. ``employee_code`` and ``punch_time`` have no sensible
    default of absent — an attendance record without them is meaningless — so they
    are required strings.
    """

    employee_code: str = "employeeCode"
    punch_time: str = "punchTime"
    punch_type: str | None = "punchType"
    verification_type: str | None = "verificationType"
    device_serial: str | None = None
    device_id: str | None = None
    client_record_id: str | None = None


class ValueMap(BaseModel):
    """Per-field translation of the bridge's enum values into the HRMS vocabulary."""

    punch_type: dict[str, str] = Field(default_factory=dict)
    verification_type: dict[str, str] = Field(default_factory=dict)


class ResponseConfig(BaseModel):
    success_status_codes: list[int] = Field(default_factory=lambda: [200, 201, 202, 204])
    #: Dotted paths into the response body. All optional — many APIs say everything
    #: they need to with the status code alone.
    success_path: str | None = None
    processed_count_path: str | None = None
    failed_count_path: str | None = None
    error_message_path: str | None = None
    #: Path to a per-record result array, for APIs that report partial acceptance.
    results_path: str | None = None
    result_id_path: str | None = None
    result_ok_path: str | None = None


class TestConnectionConfig(BaseModel):
    """How to probe HRMS without sending attendance data.

    ``path = None`` means there is no safe probe endpoint, in which case the portal
    reports that connectivity can only be proven by a real sync rather than sending
    a fabricated punch to somebody's production HRMS.
    """

    path: str | None = None
    method: str = "GET"


class HrmsProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attendance_path: str = PLACEHOLDER_PATH
    method: str = "POST"
    auth: AuthConfig = Field(default_factory=AuthConfig)
    tenant: TenantConfig = Field(default_factory=TenantConfig)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    field_map: FieldMap = Field(default_factory=FieldMap)
    value_map: ValueMap = Field(default_factory=ValueMap)
    datetime_format: DateTimeFormat = DateTimeFormat.ISO8601_OFFSET
    #: strftime pattern, used only when datetime_format is CUSTOM.
    datetime_pattern: str = "%Y-%m-%d %H:%M:%S"
    #: Timezone the HRMS expects timestamps in. UTC internally; converted on the way
    #: out so an API that wants local wall-clock time gets it.
    output_timezone: str = "Asia/Kolkata"
    response: ResponseConfig = Field(default_factory=ResponseConfig)
    test_connection: TestConnectionConfig = Field(default_factory=TestConnectionConfig)

    @field_validator("method")
    @classmethod
    def _method_is_a_write(cls, value: str) -> str:
        upper = value.upper()
        if upper not in {"POST", "PUT", "PATCH"}:
            raise ValueError("Attendance submission must use POST, PUT or PATCH.")
        return upper

    @field_validator("attendance_path")
    @classmethod
    def _path_is_relative(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("attendance_path must start with '/'.")
        return value

    @property
    def is_configured(self) -> bool:
        """False while the profile still carries the shipped placeholder."""
        return self.attendance_path != PLACEHOLDER_PATH

    @classmethod
    def default(cls) -> HrmsProfile:
        return cls()


def resolve_path(payload: Any, dotted_path: str | None) -> Any:
    """Read a value out of a response body by dotted path.

    A deliberately tiny subset of JSONPath: ``data.summary.processed`` and
    ``results.0.ok``. Enough for every response shape seen in practice, without
    taking a dependency or accepting an expression language into config.
    """
    if not dotted_path:
        return None
    current = payload
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current
