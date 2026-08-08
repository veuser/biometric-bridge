"""Device management endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminSession, CurrentSession, Db
from app.biometric.base import ConnectionTestResult
from app.biometric.device_manager import build_device, test_connection
from app.biometric.essl_client import EsslBiometricDevice
from app.core.errors import DeviceConfigurationError
from app.database.models import Device
from app.schemas.device import (
    DeviceCreate,
    DeviceInfoResponse,
    DeviceResponse,
    DeviceTestRequest,
    DeviceTestResponse,
    DeviceUpdate,
)
from app.services.device_service import DeviceService, clock_warning

router = APIRouter(prefix="/api/devices", tags=["devices"])

_service = DeviceService()


def _to_response(device: Device) -> DeviceResponse:
    payload = DeviceResponse.model_validate(device)
    # Presence only. The key itself is never returned.
    payload.comm_key_set = device.comm_key_protected is not None
    return payload


@router.get("", response_model=list[DeviceResponse])
def list_devices(db: Db, _session: CurrentSession) -> list[DeviceResponse]:
    return [_to_response(d) for d in _service.list_devices(db)]


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: Db, _admin: AdminSession) -> DeviceResponse:
    try:
        return _to_response(_service.create(db, payload))
    except DeviceConfigurationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: int, db: Db, _session: CurrentSession) -> DeviceResponse:
    try:
        return _to_response(_service.get(db, device_id))
    except DeviceConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


@router.put("/{device_id}", response_model=DeviceResponse)
def update_device(
    device_id: int, payload: DeviceUpdate, db: Db, _admin: AdminSession
) -> DeviceResponse:
    try:
        return _to_response(_service.update(db, device_id, payload))
    except DeviceConfigurationError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in exc.message.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(code, detail=exc.message) from exc


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_device(device_id: int, db: Db, _admin: AdminSession) -> None:
    try:
        _service.delete(db, device_id)
    except DeviceConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


@router.put("/{device_id}/enabled", response_model=DeviceResponse)
def set_enabled(device_id: int, enabled: bool, db: Db, _admin: AdminSession) -> DeviceResponse:
    try:
        return _to_response(_service.set_enabled(db, device_id, enabled))
    except DeviceConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


def _test_result_to_response(result: ConnectionTestResult) -> DeviceTestResponse:
    # asdict, not vars: the device dataclasses use slots and so have no __dict__.
    return DeviceTestResponse(
        reachable=result.reachable,
        message=result.message,
        info=DeviceInfoResponse(**asdict(result.info)) if result.info else None,
        clock_offset_seconds=result.clock_offset_seconds,
        clock_warning=clock_warning(result.clock_offset_seconds),
    )


@router.post("/test-connection", response_model=DeviceTestResponse)
async def test_unsaved_device(
    payload: DeviceTestRequest, _admin: AdminSession
) -> DeviceTestResponse:
    """Probe a device from the add/edit form, before it has been saved.

    Without this the operator would have to save a possibly-wrong configuration
    just to find out whether it works.
    """
    device = EsslBiometricDevice(
        ip_address=payload.ip_address,
        port=payload.port,
        timeout_seconds=payload.timeout_seconds,
        comm_key=payload.comm_key,
        force_udp=payload.force_udp,
        label=payload.name,
    )
    return _test_result_to_response(await test_connection(device))


@router.post("/{device_id}/test-connection", response_model=DeviceTestResponse)
async def test_saved_device(device_id: int, db: Db, _admin: AdminSession) -> DeviceTestResponse:
    try:
        record = _service.get(db, device_id)
        device = build_device(record)
    except DeviceConfigurationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return _test_result_to_response(await test_connection(device))
