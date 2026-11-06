"""Register HRMS employees on the terminals.

One endpoint, triggered by the **Sync new employees** button. ``dry_run`` defaults to
true: the safe call is the one you get by omission, so the terminal is only written to
when the caller says so explicitly.
"""

from __future__ import annotations

from dataclasses import asdict

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminSession, Db
from app.core.errors import (
    BridgeError,
    HrmsAuthenticationError,
    HrmsNotConfiguredError,
    HrmsTimeoutError,
    HrmsUnavailableError,
)
from app.schemas.enrollment import EnrollmentResponse
from app.services.enrollment_service import EnrollmentRunner

router = APIRouter(prefix="/api/enrollment", tags=["enrollment"])

_runner = EnrollmentRunner()


@router.post("/sync", response_model=EnrollmentResponse)
async def sync_new_employees(
    db: Db,
    _admin: AdminSession,
    dry_run: bool = Query(
        default=True,
        description="Preview who would be added without writing to any terminal.",
    ),
) -> EnrollmentResponse:
    """Add every HRMS employee that is missing from a terminal. Add-only."""
    try:
        outcome = await anyio.to_thread.run_sync(lambda: _runner.run(db, dry_run=dry_run))
    except HrmsNotConfiguredError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except HrmsAuthenticationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    except (HrmsUnavailableError, HrmsTimeoutError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    except BridgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    return EnrollmentResponse(**asdict(outcome))
