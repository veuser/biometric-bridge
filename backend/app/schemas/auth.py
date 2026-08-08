"""Request/response models for authentication and user management.

No response model here carries a password, a hash, or a session token. The token
travels only in the ``Set-Cookie`` header so page JavaScript can never read it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class CurrentUser(BaseModel):
    """Everything the portal is allowed to know about the signed-in user."""

    username: str
    role: UserRole


class BootstrapRequest(BaseModel):
    """First-run admin creation. Only accepted while no users exist."""

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class BootstrapStatus(BaseModel):
    bootstrap_required: bool


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    role: UserRole = UserRole.ADMIN


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None
    locked_until: datetime | None
