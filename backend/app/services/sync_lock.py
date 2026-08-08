"""Per-device synchronization locking.

Two layers, because they solve different failures:

* **In-process** (``threading.Lock``) stops the scheduler and an operator's "Sync
  Now" from running the same device concurrently within this process.
* **Persisted** (``sync_states.locked_at`` / ``lock_owner``) survives a crash, so a
  process killed mid-sync can be detected on the next start instead of leaving the
  device permanently wedged.

The persisted lock has a staleness timeout for exactly that reason: without one, a
Windows restart during a sync would require someone to edit SQLite by hand before
that device ever synced again.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.errors import SyncInProgressError
from app.core.logging import get_logger
from app.database.models import SyncState
from app.utils.datetime_utils import utcnow

logger = get_logger(__name__)


def process_owner() -> str:
    return f"pid:{os.getpid()}"


class DeviceLockRegistry:
    """In-process locks, one per device id, created on demand."""

    def __init__(self) -> None:
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock_for(self, device_id: int) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(device_id, threading.Lock())


_registry = DeviceLockRegistry()


@contextmanager
def device_lock(
    db: DbSession, device_id: int, *, stale_after: timedelta, owner: str | None = None
) -> Iterator[None]:
    """Hold both layers of the lock for one device, or raise 409-shaped.

    The persisted half is committed immediately so a *different* process can see it;
    holding it inside the caller's open transaction would make it invisible until
    the sync finished, which defeats the point.
    """
    owner = owner or process_owner()
    in_process = _registry.lock_for(device_id)

    if not in_process.acquire(blocking=False):
        raise SyncInProgressError(
            "Synchronization is already running for this device."
        )

    try:
        _claim_persisted_lock(db, device_id, owner=owner, stale_after=stale_after)
        try:
            yield
        finally:
            _release_persisted_lock(db, device_id)
    finally:
        in_process.release()


def _claim_persisted_lock(
    db: DbSession, device_id: int, *, owner: str, stale_after: timedelta
) -> None:
    state = db.execute(
        select(SyncState).where(SyncState.device_id == device_id)
    ).scalar_one_or_none()
    if state is None:
        state = SyncState(device_id=device_id)
        db.add(state)
        db.flush()

    now = utcnow()
    if state.locked_at is not None:
        age = now - state.locked_at
        if age < stale_after:
            raise SyncInProgressError(
                "Synchronization is already running for this device."
            )
        # Older than the staleness window: the owning process is gone.
        logger.warning(
            "Reclaiming an abandoned sync lock.",
            extra={
                "device_id": device_id,
                "previous_owner": state.lock_owner,
                "held_for_seconds": int(age.total_seconds()),
            },
        )

    state.locked_at = now
    state.lock_owner = owner
    db.commit()


def _release_persisted_lock(db: DbSession, device_id: int) -> None:
    state = db.execute(
        select(SyncState).where(SyncState.device_id == device_id)
    ).scalar_one_or_none()
    if state is not None:
        state.locked_at = None
        state.lock_owner = None
    db.commit()


def clear_locks_owned_by_this_process(db: DbSession) -> int:
    """Startup recovery.

    A lock tagged with *this* pid cannot be held by a live sync — the process has
    only just started. On Windows a pid is reused eventually, so this is scoped to
    our own owner string rather than clearing every lock, which would be unsafe if
    someone ever ran two bridges against one database.
    """
    owner = process_owner()
    states = db.execute(
        select(SyncState).where(SyncState.lock_owner == owner)
    ).scalars().all()
    for state in states:
        state.locked_at = None
        state.lock_owner = None
    if states:
        logger.info("Cleared stale sync locks from a previous run.", extra={"count": len(states)})
    db.flush()
    return len(states)
