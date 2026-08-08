"""SQLite engine and session management.

Three PRAGMAs matter for this workload and are applied to every connection:

* ``journal_mode=WAL`` — the scheduler writes while the portal reads. Without WAL
  those block each other and the UI stalls during a sync.
* ``foreign_keys=ON`` — SQLite ignores foreign keys unless asked, per connection.
* ``busy_timeout`` — makes a concurrent writer wait rather than immediately raising
  ``database is locked``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_BUSY_TIMEOUT_MS = 10_000


@event.listens_for(Engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


class DatabaseManager:
    """Owns the engine and session factory for the process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        url = settings.resolved_database_url()

        if url.startswith("sqlite") and ":memory:" not in url:
            settings.paths.data_dir.mkdir(parents=True, exist_ok=True)

        self.engine: Engine = create_engine(
            url,
            future=True,
            echo=False,
            # SQLAlchemy's default check_same_thread=True breaks the device reads,
            # which run in a worker thread pool because pyzk is blocking I/O.
            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        )
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True
        )
        logger.info("Database engine created.", extra={"database_url": _safe_url(url)})

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope: commits on success, rolls back on any exception."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _safe_url(url: str) -> str:
    """Strip any credentials before a URL reaches the log."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        return f"{scheme}://***@{rest.rpartition('@')[2]}"
    return url
