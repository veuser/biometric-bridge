"""Employee-registration API models."""

from __future__ import annotations

from pydantic import BaseModel


class AddedUserResponse(BaseModel):
    employee_code: str
    name: str
    card_set: bool
    warnings: list[str]


class FailedUserResponse(BaseModel):
    employee_code: str
    reason: str


class DeviceEnrollmentResponse(BaseModel):
    device_id: int
    device_name: str
    #: OK, PARTIAL, FAILED or SKIPPED.
    status: str
    dry_run: bool
    #: In a dry run: who WOULD be added. Nothing was written to the terminal.
    added: list[AddedUserResponse]
    already_present: int
    skipped: list[FailedUserResponse]
    failed: list[FailedUserResponse]
    error_message: str | None


class EnrollmentResponse(BaseModel):
    dry_run: bool
    employees_in_hrms: int
    devices: list[DeviceEnrollmentResponse]
