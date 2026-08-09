"""First-run seeding of the HRMS configuration row."""

from __future__ import annotations

import json

from app.database.database import DatabaseManager
from app.database.models import HrmsConnection
from app.hrms.profile import PLACEHOLDER_PATH, HrmsProfile
from app.services.hrms_service import HrmsService, load_shipped_profile


class TestShippedProfile:
    def test_it_is_a_real_contract_not_the_placeholder(self) -> None:
        profile = load_shipped_profile()
        assert profile.is_configured
        assert profile.attendance_path != PLACEHOLDER_PATH
        assert profile.attendance_path == "/api/v1/attendence/biometric"

    def test_it_round_trips_through_storage(self) -> None:
        """It is persisted as JSON and re-validated on every read, so it has to survive
        the trip. A profile that serialises to something it cannot parse would only
        surface at the first sync."""
        stored = load_shipped_profile().model_dump_json()
        assert HrmsProfile.model_validate(json.loads(stored)).is_configured


class TestSeeding:
    def test_a_fresh_database_gets_the_profile(self, db: DatabaseManager) -> None:
        with db.session() as session:
            HrmsService().seed_default_config(session)

        with db.session() as session:
            config = session.query(HrmsConnection).one()
            assert config.profile_json is not None
            assert HrmsProfile.model_validate(json.loads(config.profile_json)).is_configured

    def test_seeding_does_not_enable_sync_or_invent_a_target(self, db: DatabaseManager) -> None:
        """The row is created inert.

        Seeding a contract is safe; seeding a destination or a credential would not be —
        the bridge must never start pushing somewhere nobody pointed it at.
        """
        with db.session() as session:
            HrmsService().seed_default_config(session)

        with db.session() as session:
            config = session.query(HrmsConnection).one()
            assert config.enabled is False
            assert config.base_url == ""
            assert config.api_key_protected is None

    def test_it_never_overwrites_an_existing_configuration(self, db: DatabaseManager) -> None:
        """It runs on every startup, so an upgrade must not clobber an edited profile."""
        with db.session() as session:
            session.add(
                HrmsConnection(
                    id=1,
                    base_url="https://hrms.customer.example",
                    enabled=True,
                    profile_json=HrmsProfile(attendance_path="/custom/path").model_dump_json(),
                )
            )

        with db.session() as session:
            HrmsService().seed_default_config(session)

        with db.session() as session:
            config = session.query(HrmsConnection).one()
            assert config.base_url == "https://hrms.customer.example"
            assert config.enabled is True
            restored = HrmsProfile.model_validate(json.loads(config.profile_json))
            assert restored.attendance_path == "/custom/path"

    def test_it_is_idempotent(self, db: DatabaseManager) -> None:
        with db.session() as session:
            HrmsService().seed_default_config(session)
        with db.session() as session:
            HrmsService().seed_default_config(session)

        with db.session() as session:
            assert session.query(HrmsConnection).count() == 1
