"""Configuration and filesystem layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.paths import DATA_ROOT_ENV_VAR, AppPaths


def test_paths_are_derived_from_the_root(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "BiometricBridge")
    assert paths.data_dir == paths.root / "data"
    assert paths.logs_dir == paths.root / "logs"
    assert paths.config_dir == paths.root / "config"
    assert paths.backups_dir == paths.root / "backups"
    assert paths.database_file.parent == paths.data_dir


def test_constructing_paths_touches_no_filesystem(tmp_path: Path) -> None:
    """Import-time side effects on the filesystem would break packaging and tests."""
    root = tmp_path / "not-created"
    AppPaths(root)
    assert not root.exists()


def test_ensure_creates_the_tree_and_is_idempotent(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path / "BiometricBridge")
    paths.ensure()
    paths.ensure()
    for directory in paths.all_dirs():
        assert directory.is_dir()


def test_env_var_overrides_the_default_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, str(tmp_path / "custom"))
    assert AppPaths().root == (tmp_path / "custom").resolve()


def test_production_default_is_program_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never a temp directory — the data must survive upgrades (PLAN.md §34)."""
    monkeypatch.delenv(DATA_ROOT_ENV_VAR, raising=False)
    # SIM112 wants an upper-case name, but "ProgramData" is the actual Windows
    # variable and os.environ is case-sensitive on the lookup we do in paths.py.
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")
    assert AppPaths().root == Path(r"C:\ProgramData\BiometricBridge").resolve()


def test_database_url_is_derived_from_the_data_root(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path / "bridge"))
    url = settings.resolved_database_url()
    assert url.startswith("sqlite+pysqlite:///")
    assert "biometric_bridge.db" in url


def test_explicit_database_url_wins(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), database_url="sqlite+pysqlite:///:memory:")
    assert settings.resolved_database_url() == "sqlite+pysqlite:///:memory:"


def test_unknown_timezone_is_rejected_at_startup() -> None:
    """Better to fail on boot than at the first sync, deep inside the normalizer."""
    with pytest.raises(ValueError, match="Unknown timezone"):
        Settings(default_timezone="Mars/Olympus_Mons")


def test_default_timezone_resolves() -> None:
    """Fails without the tzdata package on Windows, which is the point."""
    assert Settings().tzinfo.key == "Asia/Kolkata"


def test_log_level_is_normalised_and_validated() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValueError, match="log_level"):
        Settings(log_level="CHATTY")


def test_portal_tls_is_off_by_default() -> None:
    """Decided 2026-08-08: plain HTTP on the LAN (PLAN.md §8)."""
    assert Settings().portal_tls_enabled is False


def test_env_vars_are_read_with_the_expected_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOMETRIC_BRIDGE_PORT", "9090")
    assert Settings().port == 9090


def test_port_must_be_in_range() -> None:
    with pytest.raises(ValueError):
        Settings(port=70000)
