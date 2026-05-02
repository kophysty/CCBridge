"""Integration tests for ccbridge.core.state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ccbridge.core.state import (
    SCHEMA_VERSION,
    CurrentIteration,
    Identity,
    State,
    clear_iteration,
    init_identity,
    load_identity,
    load_state,
    save_identity,
    save_state,
)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_load_identity_missing_returns_none(tmp_path: Path) -> None:
    assert load_identity(tmp_path / "identity.json") is None


def test_init_identity_creates_file_with_uuids(tmp_path: Path) -> None:
    path = tmp_path / ".ccbridge" / "identity.json"
    identity = init_identity(path)

    assert path.exists()
    assert identity.project_id
    assert identity.machine_id
    assert identity.schema_version == SCHEMA_VERSION


def test_init_identity_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    first = init_identity(path)
    second = init_identity(path)

    assert first.project_id == second.project_id
    assert first.machine_id == second.machine_id


def test_save_then_load_identity_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    original = Identity(project_id="proj-1", machine_id="machine-1")
    save_identity(path, original)
    loaded = load_identity(path)

    assert loaded == original


def test_load_identity_with_bom(tmp_path: Path) -> None:
    """Notepad on Windows may save with UTF-8 BOM; we strip it."""
    path = tmp_path / "identity.json"
    payload = '{"project_id":"p","machine_id":"m","schema_version":1}'
    path.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

    identity = load_identity(path)
    assert identity is not None
    assert identity.project_id == "p"


def test_load_identity_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_identity(path)


def test_load_identity_newer_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    path.write_text(
        '{"project_id":"p","machine_id":"m","schema_version":99}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="newer than supported"):
        load_identity(path)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def test_load_state_missing_returns_none(tmp_path: Path) -> None:
    assert load_state(tmp_path / "state.json") is None


def test_load_state_empty_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("", encoding="utf-8")
    assert load_state(path) is None


def test_save_then_load_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = State()
    save_state(path, state)
    loaded = load_state(path)

    assert loaded is not None
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.current_iteration is None


def test_save_then_load_with_iteration(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    iteration = CurrentIteration(
        id="iter-1",
        started_at=datetime(2026, 5, 2, 10, 0, 0, tzinfo=UTC),
        iteration_count=2,
        max_iterations=3,
        last_verdict="fail",
        diff_blob_shas=("a1b2", "c3d4"),
    )
    save_state(path, State(current_iteration=iteration))
    loaded = load_state(path)

    assert loaded is not None
    assert loaded.current_iteration is not None
    assert loaded.current_iteration.id == "iter-1"
    assert loaded.current_iteration.iteration_count == 2
    assert loaded.current_iteration.last_verdict == "fail"
    assert loaded.current_iteration.diff_blob_shas == ("a1b2", "c3d4")


def test_load_state_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_state(path)


def test_load_state_newer_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        '{"schema_version":99,"current_iteration":null}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="newer than supported"):
        load_state(path)


def test_clear_iteration_keeps_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    iteration = CurrentIteration(
        id="i",
        started_at=datetime.now(UTC),
        iteration_count=1,
        max_iterations=3,
    )
    save_state(path, State(current_iteration=iteration))

    clear_iteration(path)
    loaded = load_state(path)

    assert loaded is not None
    assert loaded.current_iteration is None
    assert loaded.schema_version == SCHEMA_VERSION


def test_clear_iteration_on_missing_file_creates_empty_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    clear_iteration(path)

    loaded = load_state(path)
    assert loaded is not None
    assert loaded.current_iteration is None


# ---------------------------------------------------------------------------
# Atomic write properties
# ---------------------------------------------------------------------------


def test_save_state_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "deeply" / "nested" / "state.json"
    save_state(path, State())
    assert path.exists()


def test_save_state_does_not_leave_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, State())
    save_state(path, State())
    save_state(path, State())

    # Only the target file should remain — no `.tmp` siblings.
    siblings = list(tmp_path.iterdir())
    assert siblings == [path]


def test_save_state_overwrites_existing(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    iteration1 = CurrentIteration(
        id="first",
        started_at=datetime.now(UTC),
        iteration_count=1,
        max_iterations=3,
    )
    save_state(path, State(current_iteration=iteration1))

    iteration2 = CurrentIteration(
        id="second",
        started_at=datetime.now(UTC),
        iteration_count=2,
        max_iterations=3,
    )
    save_state(path, State(current_iteration=iteration2))

    loaded = load_state(path)
    assert loaded is not None
    assert loaded.current_iteration is not None
    assert loaded.current_iteration.id == "second"


def test_unicode_in_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    iteration = CurrentIteration(
        id="итерация-кириллица",
        started_at=datetime.now(UTC),
        iteration_count=1,
        max_iterations=3,
        last_verdict="fail",
    )
    save_state(path, State(current_iteration=iteration))

    raw = path.read_text(encoding="utf-8")
    assert "итерация-кириллица" in raw

    loaded = load_state(path)
    assert loaded is not None
    assert loaded.current_iteration is not None
    assert loaded.current_iteration.id == "итерация-кириллица"
