"""Tests for ccbridge.core.migrations."""

from __future__ import annotations

from pathlib import Path

import pytest

from ccbridge.core import migrations
from ccbridge.core.migrations import (
    CURRENT_SCHEMA_VERSION,
    UnknownSchemaError,
    backup_file,
    migrate,
    register_migration,
)


@pytest.fixture(autouse=True)
def _restore_migrations_registry() -> None:
    """Each test gets a clean registry; restore after."""
    saved = dict(migrations._MIGRATIONS)
    migrations._MIGRATIONS.clear()
    yield
    migrations._MIGRATIONS.clear()
    migrations._MIGRATIONS.update(saved)


def test_migrate_noop_when_already_at_target() -> None:
    data = {"schema_version": 1, "x": 1}
    result = migrate(data, target_version=1)
    assert result == data


def test_migrate_missing_schema_version_assumes_v1() -> None:
    data = {"x": 1}  # no schema_version field
    result = migrate(data, target_version=1)
    # Returned without modification (other than no upgrade needed).
    assert result["x"] == 1


def test_migrate_v1_to_v2_via_registered_step() -> None:
    @register_migration(1, 2)
    def step(data: dict) -> dict:  # type: ignore[type-arg]
        data["new_field"] = "default"
        return data

    out = migrate({"schema_version": 1, "x": 1}, target_version=2)
    assert out["schema_version"] == 2
    assert out["new_field"] == "default"
    assert out["x"] == 1


def test_migrate_chains_multiple_steps() -> None:
    @register_migration(1, 2)
    def v1_to_v2(data: dict) -> dict:  # type: ignore[type-arg]
        data["v2_added"] = True
        return data

    @register_migration(2, 3)
    def v2_to_v3(data: dict) -> dict:  # type: ignore[type-arg]
        data["v3_added"] = True
        return data

    out = migrate({"schema_version": 1}, target_version=3)
    assert out["schema_version"] == 3
    assert out["v2_added"] is True
    assert out["v3_added"] is True


def test_migrate_unknown_step_raises() -> None:
    with pytest.raises(UnknownSchemaError, match="no migration registered for 1 → 2"):
        migrate({"schema_version": 1}, target_version=2)


def test_migrate_downgrade_raises() -> None:
    with pytest.raises(UnknownSchemaError, match="cannot downgrade"):
        migrate({"schema_version": 5}, target_version=1)


def test_current_schema_version_constant_is_1_for_v0_1() -> None:
    """Sanity check for v0.1 — there is only one schema version."""
    assert CURRENT_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# backup_file
# ---------------------------------------------------------------------------


def test_backup_file_creates_bak_copy(tmp_path: Path) -> None:
    src = tmp_path / "state.json"
    src.write_text('{"x": 1}', encoding="utf-8")

    backup = backup_file(src)

    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == '{"x": 1}'
    assert src.exists()  # original preserved


def test_backup_file_increments_when_target_exists(tmp_path: Path) -> None:
    src = tmp_path / "state.json"
    src.write_text("v1", encoding="utf-8")

    first = backup_file(src)
    src.write_text("v2", encoding="utf-8")
    second = backup_file(src)

    assert first != second
    assert first.read_text(encoding="utf-8") == "v1"
    assert second.read_text(encoding="utf-8") == "v2"


def test_backup_file_missing_source_returns_path_without_error(tmp_path: Path) -> None:
    src = tmp_path / "missing.json"
    backup = backup_file(src)
    # Just returns the would-be path; nothing is created.
    assert not backup.exists()
