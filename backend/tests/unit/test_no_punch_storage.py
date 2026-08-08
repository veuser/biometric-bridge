"""RULE 1 — no biometric punch data is ever persisted.

The bridge is a conduit, not a store. Punches live in memory for the duration of a
sync run and are then discarded; SQLite keeps configuration, sync state and *counts*.

This test exists so that adding a well-meaning "just cache the punches for the
dashboard" column fails loudly in CI rather than quietly creating a biometric data
store on a customer's file server.
"""

from __future__ import annotations

from app.database.models import Base

#: Column-name fragments that would indicate punch data has been persisted.
FORBIDDEN_FRAGMENTS = (
    "punch_time",
    "employee_code",
    "user_id",
    "fingerprint",
    "template",
    "face",
    "biometric_log",
    "attendance_record",
)

#: Legitimate uses of an otherwise-suspicious word.
ALLOWED_EXACT = {
    ("sync_runs", "records_sent"),
    ("sync_runs", "records_success"),
    ("sync_runs", "records_failed"),
    ("sync_runs", "records_invalid"),
    ("sync_runs", "records_suppressed"),
    ("sync_runs", "buffer_records"),
    ("sync_runs", "window_records"),
}


def test_no_table_stores_punch_fields() -> None:
    offenders: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            if (table_name, column.name) in ALLOWED_EXACT:
                continue
            lowered = column.name.lower()
            for fragment in FORBIDDEN_FRAGMENTS:
                if fragment in lowered:
                    offenders.append(f"{table_name}.{column.name} (matched {fragment!r})")
    assert not offenders, (
        "Biometric punch data must never be persisted (RULE 1). Offending columns: "
        + ", ".join(offenders)
    )


def test_no_table_is_named_after_punches() -> None:
    forbidden_tables = {"punches", "attendance", "attendance_logs", "biometric_logs"}
    assert not (set(Base.metadata.tables) & forbidden_tables)


def test_expected_tables_are_the_only_tables() -> None:
    """A new table should be a deliberate decision, reviewed against RULE 1."""
    assert set(Base.metadata.tables) == {
        "devices",
        "hrms_connections",
        "sync_states",
        "sync_runs",
        "app_settings",
        "users",
    }
