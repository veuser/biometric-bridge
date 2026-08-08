"""Portal user management. Administrator only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import AdminSession, Db, get_auth_service
from app.database.models import User
from app.schemas.auth import PasswordChangeRequest, UserCreateRequest, UserResponse
from app.services.auth_service import AuthError, AuthService

router = APIRouter(prefix="/api/users", tags=["users"])

AuthDep = Annotated[AuthService, Depends(get_auth_service)]


@router.get("", response_model=list[UserResponse])
def list_users(db: Db, _admin: AdminSession) -> list[User]:
    return list(db.execute(select(User).order_by(User.username)).scalars())


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreateRequest, db: Db, auth: AuthDep, _admin: AdminSession) -> User:
    try:
        return auth.create_user(
            db, username=payload.username, password=payload.password, role=payload.role
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def change_password(
    user_id: int, payload: PasswordChangeRequest, db: Db, auth: AuthDep, _admin: AdminSession
) -> None:
    try:
        auth.change_password(db, user_id=user_id, new_password=payload.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{user_id}/active", response_model=UserResponse)
def set_active(user_id: int, is_active: bool, db: Db, auth: AuthDep, admin: AdminSession) -> User:
    if user_id == admin.user_id and not is_active:
        # Locking the last administrator out of the portal would require database
        # surgery to undo, on a machine nobody has tooling for.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )
    try:
        return auth.set_active(db, user_id=user_id, is_active=is_active)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
