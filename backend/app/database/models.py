"""ORM models.

**Invariant: no table stores a biometric punch.** Punches exist only in memory for
the duration of a sync run (PLAN.md §0, RULE 1). What persists is configuration, sync
state, and per-run *counts*. ``tests/unit/test_no_punch_storage.py`` enforces this so
it cannot regress unnoticed.

All timestamps are stored as timezone-aware UTC. SQLite has no native timestamp type
and will happily round-trip a naive datetime, so :class:`UtcDateTime` normalises on
the way in and re-attaches UTC on the way out.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.enums import DeviceRole, DeviceType, SyncStatus, SyncTrigger, UserRole


class UtcDateTime(TypeDecorator[datetime]):
    """A DateTime that refuses naive values and always returns UTC-aware ones.

    This is the mechanical half of the "never compare naive and aware datetimes"
    rule (PLAN.md §5.3, spec RULE 15) — the database layer simply will not accept a
    naive value, so the rule cannot be broken by forgetting.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime reached the database layer; convert to UTC first "
                "(see app.utils.datetime_utils)"
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("ip_address", "port", name="uq_device_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=4370)
    serial_number: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(120))
    device_type: Mapped[DeviceType] = mapped_column(
        String(32), nullable=False, default=DeviceType.ESSL
    )
    role: Mapped[DeviceRole] = mapped_column(
        String(32), nullable=False, default=DeviceRole.CHECK_IN
    )

    #: ZK device communication key, wrapped by the SecretProtector seam. This is the
    #: device's own comm password, not any employee credential.
    comm_key_protected: Mapped[bytes | None] = mapped_column(LargeBinary)

    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    force_udp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Per-device so a multi-site deployment can span timezones. NULL means "use the
    #: application default".
    timezone: Mapped[str | None] = mapped_column(String(64))

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    sync_state: Mapped[SyncState | None] = relationship(
        back_populates="device", cascade="all, delete-orphan", uselist=False
    )
    sync_runs: Mapped[list[SyncRun]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Device {self.id} {self.name!r} {self.ip_address}:{self.port}>"


class HrmsConnection(TimestampMixin, Base):
    """The single HRMS endpoint configuration (one row, id=1)."""

    __tablename__ = "hrms_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)

    #: Wrapped by the SecretProtector seam. Currently pass-through — see PLAN.md §8.
    api_key_protected: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: Last few characters, so the UI can show a masked value without ever revealing
    #: or round-tripping the key itself.
    api_key_last4: Mapped[str | None] = mapped_column(String(8))

    tenant_identifier: Mapped[str | None] = mapped_column(String(120))

    #: The HRMS profile (PLAN.md §6) as JSON. Stored rather than shipped in code so
    #: the contract can be wired without a redeploy.
    profile_json: Mapped[str | None] = mapped_column(Text)

    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SyncState(TimestampMixin, Base):
    """Per-device watermark and lock. The most important state in the application."""

    __tablename__ = "sync_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    #: Max punch time HRMS has accepted for this device. Advanced only after a fully
    #: successful push (RULE 3). NULL until the first punch is pushed.
    last_successful_push_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    last_sync_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_status: Mapped[SyncStatus | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(Text)

    #: Persisted half of the two-layer lock. Survives a process crash so startup can
    #: detect and clear an abandoned run instead of wedging the device.
    locked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    lock_owner: Mapped[str | None] = mapped_column(String(120))

    #: device_clock - bridge_clock, in seconds, from the most recent run. Diagnostic,
    #: and the input to the clock-regression guard.
    clock_offset_seconds: Mapped[float | None] = mapped_column(Float)

    device: Mapped[Device] = relationship(back_populates="sync_state")


class SyncRun(Base):
    """Execution metadata for one sync attempt. Counts only — never punch data."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[SyncTrigger] = mapped_column(String(32), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    from_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    to_time: Mapped[datetime | None] = mapped_column(UtcDateTime)

    #: Size of the whole device buffer that had to be transferred. The ZK protocol
    #: has no server-side time filter, so this is the real cost driver (PLAN.md §1.2)
    #: and is deliberately tracked separately from the in-window count.
    buffer_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    records_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Suppressed by the already-pushed LRU (PLAN.md §5.8).
    records_suppressed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    device_read_ms: Mapped[int | None] = mapped_column(Integer)
    hrms_ms: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[SyncStatus] = mapped_column(String(40), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text)

    device: Mapped[Device] = relationship(back_populates="sync_runs")

    @property
    def duration_ms(self) -> int | None:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at).total_seconds() * 1000)


class AppSetting(TimestampMixin, Base):
    """Operator-tunable runtime settings, editable in the portal without a restart."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class User(TimestampMixin, Base):
    """Portal login. Phase 2 fills in the auth flow; the table lands with Phase 1."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.ADMIN)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
