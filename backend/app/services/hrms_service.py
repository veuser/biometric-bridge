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
from pathlib import Path

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

#: Profile seeded into a brand-new database. Bundled by PyInstaller (see the
#: ``app/hrms/profiles`` entry in ``biometric_bridge.spec``), so this resolves in a
#: frozen build as well as from source.
SHIPPED_PROFILE_PATH = Path(__file__).resolve().parent.parent / "hrms" / "profiles" / "vectoredge-hrms.json"


def load_shipped_profile() -> HrmsProfile:
    """The VectorEdge HRMS contract, or the placeholder template if it cannot be read.

    Seeding a real contract is not the same as *guessing* one — the endpoint this
    describes was built alongside the bridge, so both ends of it are ours. The
    placeholder in :meth:`HrmsProfile.default` still exists for the case it was written
    for: an unknown HRMS, where the bridge must refuse to invent a path.

    Falls back rather than raising, because a missing or corrupt profile file must not
    stop the service from starting. The operator can still paste a profile into the
    portal, which is exactly the state a fresh install used to be in.
    """
    try:
        return HrmsProfile.model_validate(json.loads(SHIPPED_PROFILE_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.warning(
            "Could not load the shipped HRMS profile; falling back to the placeholder "
            "template. Configure the profile on the HRMS page.",
            extra={"path": str(SHIPPED_PROFILE_PATH), "error": str(exc)},
        )
        return HrmsProfile.default()


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

    def seed_default_config(self, db: DbSession) -> None:
        """Create the singleton row on first run, pre-loaded with the shipped profile.

        Without this a fresh install opens the HRMS page with an empty profile box and
        the operator has to paste ~50 lines of JSON correctly before anything works —
        a step that is pure transcription risk, since the contract is fixed and known.
        Seeding leaves exactly two fields to fill in: the base URL and the API key.

        Deliberately does NOT enable sync. The row is created disabled and with no
        credential, so seeding cannot cause the bridge to start pushing to somewhere
        it was never pointed at. The existing guards still apply: a base URL and a key
        are both required before the enable checkbox is accepted.

        No-ops when a row already exists, so an upgrade never overwrites a profile an
        operator has edited.
        """
        if self.get_config(db) is not None:
            return

        profile = load_shipped_profile()
        db.add(
            HrmsConnection(
                id=CONFIG_ID,
                base_url="",
                enabled=False,
                profile_json=profile.model_dump_json(),
            )
        )
        db.flush()
        logger.info(
            "Seeded the HRMS configuration row with the shipped profile.",
            extra={"attendance_path": profile.attendance_path},
        )

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
