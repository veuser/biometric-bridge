"""Application-level configuration.

Split of responsibilities, per PLAN.md §33:

* **This module** holds deployment facts that must be known before the database
  exists — host, port, data root, log level, timezone default. Sourced from
  environment variables / ``.env``.
* **The database** (``app_settings`` table, Phase 5) holds operator-tunable runtime
  settings — sync interval, overlap, retry policy. Those are edited in the portal and
  must not require a service restart.

The HRMS API key is deliberately *not* here: it is managed through the portal and
lives in ``hrms_connections``.
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import AppPaths


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIOMETRIC_BRIDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="production")

    # Bind on all interfaces: the portal is reached from other LAN machines. The
    # install script scopes the firewall rule to the local subnet (PLAN.md §37) —
    # that, not the bind address, is what limits exposure.
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=10255, ge=1, le=65535)

    # Portal runs over plain HTTP on the LAN (PLAN.md §8, decided 2026-08-08).
    # Flipping this to True is all that is needed to make the session cookie Secure
    # and enable HSTS once a certificate is available.
    portal_tls_enabled: bool = Field(default=False)

    default_timezone: str = Field(default="Asia/Kolkata")

    #: Minimum portal password length. Lower it for a lab or a demo; raise it to
    #: 12 or more to also enable the common-password and character-variety checks.
    min_password_length: int = Field(default=4, ge=1, le=128)

    log_level: str = Field(default="INFO")
    log_max_bytes: int = Field(default=10 * 1024 * 1024)
    log_backup_count: int = Field(default=10)
    log_to_stdout: bool = Field(default=True)

    #: Overrides the computed SQLite path. Mainly for tests.
    database_url: str | None = Field(default=None)

    data_root: str | None = Field(default=None)

    @field_validator("default_timezone")
    @classmethod
    def _timezone_must_resolve(cls, value: str) -> str:
        # Fail at startup with a clear message rather than at the first sync with a
        # ZoneInfoNotFoundError from deep inside the normalizer.
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Unknown timezone {value!r}. On Windows this usually means the "
                "'tzdata' package is missing from the environment."
            ) from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level_is_valid(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @property
    def paths(self) -> AppPaths:
        from pathlib import Path

        return AppPaths(Path(self.data_root) if self.data_root else None)

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.default_timezone)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.paths.database_file}"


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached because ``Settings()`` re-reads the environment and ``.env`` on every
    construction. Tests call ``get_settings.cache_clear()``.
    """
    return Settings()
