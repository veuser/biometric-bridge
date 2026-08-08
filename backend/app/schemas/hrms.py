"""HRMS configuration API models.

No model here can carry the API key outward. ``HrmsConfigResponse`` exposes only a
masked hint and a boolean, which is enough for the portal to show "configured" and
let the operator confirm they pasted the right key.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.hrms.profile import HrmsProfile


class HrmsConfigRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    #: Write-only. Omit to keep the stored key unchanged.
    api_key: str | None = Field(default=None, max_length=512)
    tenant_identifier: str | None = Field(default=None, max_length=120)
    profile: HrmsProfile = Field(default_factory=HrmsProfile.default)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=120)
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)
    enabled: bool = False
    #: Opt out of the HTTPS requirement for a lab/staging HRMS.
    allow_insecure_http: bool = False

    @field_validator("base_url")
    @classmethod
    def _looks_like_a_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://")
        return value.rstrip("/")


class HrmsConfigResponse(BaseModel):
    base_url: str
    tenant_identifier: str | None
    #: e.g. "••••••••3f9c". Never the key itself.
    api_key_masked: str
    api_key_set: bool
    profile: HrmsProfile
    connect_timeout_seconds: int
    request_timeout_seconds: int
    enabled: bool
    #: False while the profile still carries the shipped placeholder path.
    profile_configured: bool


class HrmsTestResponse(BaseModel):
    reachable: bool
    message: str
