"""SQLite backups.

Uses SQLite's online backup API rather than copying the file. Under WAL a plain file
copy can capture a database mid-transaction, or miss committed data still sitting in
the ``-wal`` file — producing a backup that restores to a corrupt or stale state,
which is discovered only when someone needs it.

The backup contains the HRMS API key. With encryption at rest currently off
(PLAN.md §8) that means the backup directory needs the same protection as the data
directory; this is stated in the installation docs rather than left implicit.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger
from app.utils.datetime_utils import utcnow

logger = get_logger(__name__)

BACKUP_PREFIX = "biometric_bridge-"
BACKUP_SUFFIX = ".db"


class BackupService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create(self, *, keep: int = 7) -> Path:
        paths = self._settings.paths
        paths.ensure()

        # Millisecond precision: two backups taken in the same second would
        # otherwise share a filename and silently overwrite each other.
        stamp = utcnow().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        target = paths.backups_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"

        source = sqlite3.connect(paths.database_file)
        try:
            destination = sqlite3.connect(target)
            try:
                # Consistent even while the scheduler is writing.
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()

        logger.info(
            "Backup created.",
            extra={"backup_file": str(target), "size_bytes": target.stat().st_size},
        )
        self.prune(keep=keep)
        return target

    def list_backups(self) -> list[Path]:
        directory = self._settings.paths.backups_dir
        if not directory.is_dir():
            return []
        return sorted(
            directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"),
            key=lambda p: p.name,
            reverse=True,
        )

    def prune(self, *, keep: int) -> int:
        """Delete all but the newest ``keep`` backups.

        Bounded on purpose: this runs unattended on a machine nobody watches, and an
        unbounded backup directory eventually fills the disk that the live database
        also sits on.
        """
        if keep < 1:
            return 0
        doomed = self.list_backups()[keep:]
        for path in doomed:
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not remove old backup.", extra={"backup_file": str(path)})
        if doomed:
            logger.info("Pruned old backups.", extra={"removed": len(doomed)})
        return len(doomed)

    @staticmethod
    def describe(path: Path) -> dict[str, object]:
        stat = path.stat()
        return {
            "name": path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        }
