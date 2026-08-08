"""Filesystem layout.

Production data lives under ``C:\\ProgramData\\BiometricBridge`` so it survives
application upgrades and is reachable by the service account (which has no user
profile). Never a temp directory — see PLAN.md §34.

Development falls back to ``<repo>/.local`` so running from a checkout never writes
into ProgramData or requires an elevated shell.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "BiometricBridge"

#: Set this to point the whole application at a different data root. The install
#: scripts and the test suite both use it.
DATA_ROOT_ENV_VAR = "BIOMETRIC_BRIDGE_DATA_ROOT"


def _default_data_root() -> Path:
    """ProgramData on Windows, a home-relative path elsewhere (dev machines, CI).

    Windows normalises environment keys to upper case, so ``PROGRAMDATA`` is the
    canonical spelling even though the variable is displayed as ``ProgramData``.
    """
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        return Path(program_data) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


class AppPaths:
    """Resolved application directories.

    Construction is side-effect free; call :meth:`ensure` to create the tree. That
    split matters because importing the module must not touch the filesystem.
    """

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            env_root = os.environ.get(DATA_ROOT_ENV_VAR)
            root = Path(env_root) if env_root else _default_data_root()
        self.root = root.resolve()

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "biometric_bridge.db"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "biometric_bridge.log"

    def all_dirs(self) -> tuple[Path, ...]:
        return (self.root, self.data_dir, self.logs_dir, self.config_dir, self.backups_dir)

    def ensure(self) -> None:
        """Create the directory tree. Idempotent."""
        for directory in self.all_dirs():
            directory.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"AppPaths(root={self.root!s})"
