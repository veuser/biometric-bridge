"""Health reporting.

Deliberately never raises. A health check that throws when a subsystem is down is
useless precisely when it is needed — the caller cannot distinguish "database is
broken" from "health endpoint is broken". Each probe returns a component result and
the overall status is derived from them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.core.logging import get_logger
from app.database.database import DatabaseManager
from app.utils.datetime_utils import isoformat_utc, utcnow

logger = get_logger(__name__)


@dataclass(slots=True)
class ComponentHealth:
    name: str
    healthy: bool
    detail: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"healthy": self.healthy}
        if self.detail:
            payload["detail"] = self.detail
        payload.update(self.data)
        return payload


class HealthService:
    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def check_database(self) -> ComponentHealth:
        try:
            with self._db.session() as session:
                session.execute(text("SELECT 1"))
                journal_mode = session.execute(text("PRAGMA journal_mode")).scalar_one()
            return ComponentHealth("database", True, data={"journal_mode": journal_mode})
        except Exception as exc:
            logger.exception("Database health check failed.")
            return ComponentHealth("database", False, detail=_safe_detail(exc))

    def detailed(self) -> dict[str, Any]:
        components = [self.check_database()]
        return {
            "status": "healthy" if all(c.healthy for c in components) else "unhealthy",
            "checked_at": isoformat_utc(utcnow()),
            "components": {c.name: c.to_dict() for c in components},
        }


def _safe_detail(exc: Exception) -> str:
    """Exception type only — the message can carry a path or a connection string."""
    return type(exc).__name__
