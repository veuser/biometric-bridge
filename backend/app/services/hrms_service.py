"""HRMS connection configuration.

The API key is handled under one rule: it enters through ``save_config`` and leaves
only through ``reveal_api_key`` at the moment a request is built. No other path
returns it, and every write registers it with the log redactor so it cannot appear
in the log file even if some future code path logs a URL or an exception containing
it. That redaction is load-bearing, because encryption at rest is currently off
(PLAN.md §8).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.errors import HrmsNotConfiguredError
from app.core.logging import get_logger, register_secret, unregister_secret
from app.database.models import HrmsConnection
from app.hrms.client import HrmsClient
from app.hrms.profile import HrmsProfile
from app.services.secret_protector import SecretProtector, get_secret_protector

logger = get_logger(__name__)

#: The configuration is a singleton row; there is one HRMS.
CONFIG_ID = 1


class HrmsService:
    def __init__(self, protector: SecretProtector | None = None) -> None:
        self._protector = protector or get_secret_protector()

    # ── persistence ──────────────────────────────────────────────────────────

    def get_config(self, db: DbSession) -> HrmsConnection | None:
        return db.execute(select(HrmsConnection).limit(1)).scalar_one_or_none()

    def require_config(self, db: DbSession) -> HrmsConnection:
        config = self.get_config(db)
        if config is None:
            raise HrmsNotConfiguredError("HRMS has not been configured yet.")
        return config

    def save_config(
        self,
        db: DbSession,
        *,
        base_url: str,
        api_key: str | None,
        tenant_identifier: str | None,
        profile: HrmsProfile,
        connect_timeout_seconds: int,
        request_timeout_seconds: int,
        enabled: bool,
    ) -> HrmsConnection:
        config = self.get_config(db)
        if config is None:
            config = HrmsConnection(id=CONFIG_ID, base_url=base_url)
            db.add(config)

        config.base_url = base_url.rstrip("/")
        config.tenant_identifier = tenant_identifier
        config.profile_json = profile.model_dump_json()
        config.connect_timeout_seconds = connect_timeout_seconds
        config.request_timeout_seconds = request_timeout_seconds
        config.enabled = enabled

        # An omitted key means "keep the existing one". The form cannot pre-fill a
        # write-only field, so treating omission as deletion would wipe the key on
        # every unrelated edit.
        if api_key:
            previous = self._reveal_or_none(config)
            if previous:
                unregister_secret(previous)
            config.api_key_protected = self._protector.protect(api_key)
            config.api_key_last4 = api_key[-4:]
            register_secret(api_key)

        db.flush()
        logger.info(
            "HRMS configuration saved.",
            extra={
                "base_url": config.base_url,
                "enabled": config.enabled,
                "api_key_set": config.api_key_protected is not None,
            },
        )
        return config

    def clear_api_key(self, db: DbSession) -> None:
        config = self.require_config(db)
        previous = self._reveal_or_none(config)
        if previous:
            unregister_secret(previous)
        config.api_key_protected = None
        config.api_key_last4 = None
        config.enabled = False
        db.flush()

    # ── use ──────────────────────────────────────────────────────────────────

    def load_profile(self, config: HrmsConnection) -> HrmsProfile:
        if not config.profile_json:
            return HrmsProfile.default()
        return HrmsProfile.model_validate(json.loads(config.profile_json))

    def reveal_api_key(self, config: HrmsConnection) -> str:
        """The only path that returns the key. Callers must not retain the value."""
        if not config.api_key_protected:
            raise HrmsNotConfiguredError("No HRMS API key has been configured.")
        return self._protector.reveal(config.api_key_protected)

    def build_client(self, config: HrmsConnection) -> HrmsClient:
        return HrmsClient(
            base_url=config.base_url,
            profile=self.load_profile(config),
            connect_timeout=config.connect_timeout_seconds,
            request_timeout=config.request_timeout_seconds,
        )

    def register_existing_secret(self, db: DbSession) -> None:
        """Re-register the stored key with the log redactor at startup.

        Redaction state lives in process memory, so without this the key would be
        unprotected in the log until the next time someone saved the configuration.
        """
        config = self.get_config(db)
        if config is None:
            return
        key = self._reveal_or_none(config)
        if key:
            register_secret(key)
            logger.info("HRMS API key registered with the log redactor.")

    def _reveal_or_none(self, config: HrmsConnection) -> str | None:
        if not config.api_key_protected:
            return None
        try:
            return self._protector.reveal(config.api_key_protected)
        except (ValueError, TypeError):
            logger.warning("Stored HRMS API key could not be read; it may need re-entering.")
            return None
