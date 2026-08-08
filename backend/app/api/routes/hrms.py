"""HRMS configuration endpoints."""

from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminSession, CurrentSession, Db
from app.core.errors import HrmsNotConfiguredError
from app.database.models import HrmsConnection
from app.schemas.hrms import HrmsConfigRequest, HrmsConfigResponse, HrmsTestResponse
from app.services.hrms_service import HrmsService

router = APIRouter(prefix="/api/hrms", tags=["hrms"])

_service = HrmsService()


def _to_response(config: HrmsConnection, service: HrmsService) -> HrmsConfigResponse:
    profile = service.load_profile(config)
    # Built from the stored last-4 hint, never by decrypting and re-masking the key.
    masked = "•" * 8 + config.api_key_last4 if config.api_key_last4 else ""
    return HrmsConfigResponse(
        base_url=config.base_url,
        tenant_identifier=config.tenant_identifier,
        api_key_masked=masked,
        api_key_set=config.api_key_protected is not None,
        profile=profile,
        connect_timeout_seconds=config.connect_timeout_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
        enabled=config.enabled,
        profile_configured=profile.is_configured,
    )


@router.get("/config", response_model=HrmsConfigResponse)
def get_config(db: Db, _session: CurrentSession) -> HrmsConfigResponse:
    config = _service.get_config(db)
    if config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="HRMS has not been configured yet."
        )
    return _to_response(config, _service)


@router.put("/config", response_model=HrmsConfigResponse)
def save_config(payload: HrmsConfigRequest, db: Db, _admin: AdminSession) -> HrmsConfigResponse:
    if payload.base_url.startswith("http://") and not payload.allow_insecure_http:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "HRMS must be reached over HTTPS. The API key would otherwise cross "
                "the network in clear text. Tick 'allow insecure HTTP' only for a "
                "lab environment."
            ),
        )

    if payload.enabled and not payload.profile.is_configured:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "Set the HRMS attendance endpoint path before enabling sync. "
                "The bridge will not guess your API contract."
            ),
        )

    existing = _service.get_config(db)
    if payload.enabled and not payload.api_key and not (
        existing and existing.api_key_protected
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="An API key is required to enable sync."
        )

    config = _service.save_config(
        db,
        base_url=payload.base_url,
        api_key=payload.api_key,
        tenant_identifier=payload.tenant_identifier,
        profile=payload.profile,
        connect_timeout_seconds=payload.connect_timeout_seconds,
        request_timeout_seconds=payload.request_timeout_seconds,
        enabled=payload.enabled,
    )
    return _to_response(config, _service)


@router.post("/test-connection", response_model=HrmsTestResponse)
async def test_connection(db: Db, _admin: AdminSession) -> HrmsTestResponse:
    """Probe the configured HRMS. Never sends fabricated attendance data."""
    try:
        config = _service.require_config(db)
        api_key = _service.reveal_api_key(config)
        client = _service.build_client(config)
    except HrmsNotConfiguredError as exc:
        return HrmsTestResponse(reachable=False, message=exc.message)

    tenant = config.tenant_identifier
    # httpx.Client is synchronous here; keep it off the event loop.
    reachable, message = await anyio.to_thread.run_sync(
        lambda: client.test_connection(api_key=api_key, tenant=tenant)
    )
    return HrmsTestResponse(reachable=reachable, message=message)
