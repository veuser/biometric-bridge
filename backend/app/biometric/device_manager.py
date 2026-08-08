"""Builds device connectors and keeps their blocking I/O off the event loop.

pyzk is synchronous socket I/O and a full buffer read can take seconds. Awaiting it
directly on the event loop would freeze every other request — the portal would stall
for the whole duration of each poll. Every device call therefore goes through
``anyio.to_thread.run_sync``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

import anyio.to_thread

from app.biometric.base import (
    AttendanceReadResult,
    BiometricDevice,
    ConnectionTestResult,
    DeviceInfo,
)
from app.biometric.essl_client import EsslBiometricDevice
from app.core.enums import DeviceType
from app.core.errors import DeviceConfigurationError
from app.database.models import Device
from app.services.secret_protector import SecretProtector, get_secret_protector

T = TypeVar("T")


def build_device(device: Device, protector: SecretProtector | None = None) -> BiometricDevice:
    """Construct the connector for a configured device row."""
    if DeviceType(device.device_type) is not DeviceType.ESSL:
        raise DeviceConfigurationError(
            f"Device type {device.device_type!r} is not supported by this version."
        )

    protector = protector or get_secret_protector()
    comm_key = (
        protector.reveal(device.comm_key_protected) if device.comm_key_protected else None
    )

    return EsslBiometricDevice(
        ip_address=device.ip_address,
        port=device.port,
        timeout_seconds=device.timeout_seconds,
        comm_key=comm_key,
        force_udp=device.force_udp,
        label=device.name,
    )


async def run_device_io(operation: Callable[[], T]) -> T:
    """Execute a blocking device call in a worker thread."""
    return await anyio.to_thread.run_sync(operation)


async def test_connection(device: BiometricDevice) -> ConnectionTestResult:
    return await run_device_io(device.test_connection)


async def get_device_info(device: BiometricDevice) -> DeviceInfo:
    return await run_device_io(device.get_device_info)


async def get_device_time(device: BiometricDevice) -> datetime:
    return await run_device_io(device.get_device_time)


async def get_attendance_logs(
    device: BiometricDevice, from_time: datetime, to_time: datetime
) -> AttendanceReadResult:
    return await run_device_io(lambda: device.get_attendance_logs(from_time, to_time))
