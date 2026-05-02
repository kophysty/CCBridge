"""Project state and identity persistence.

`state.json` is a *cache* of orchestrator state — the audit log is the
source of truth (see ARCHITECTURE.md §2.4 and `audit_log.py`). We persist
state for fast `ccbridge status` queries; if the cache is missing or
stale, callers reconstruct from the last events of the audit log.

`identity.json` carries the stable `project_id` (UUID) plus a per-machine
identifier. Lives next to the state file but is intentionally separate:

* `identity.json` is in `.gitignore` — never shared between machines.
* `state.json` could theoretically be checked in (we don't, but it's
  not a privacy hazard).

Atomic write strategy (closes audit P0-1, sd-#1, sd-#21):

* Write to a temp file *in the same directory* (so `os.replace` is
  atomic across all platforms — Windows requires same volume).
* `os.replace` is atomic by spec on POSIX; on Windows it is atomic
  for files on the same filesystem.
* If the target is currently open by a reader on Windows, `os.replace`
  may raise `PermissionError`. We retry once after a short delay.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """Per-project, per-machine identity. Lives in `.ccbridge/identity.json`.

    `project_id` is stable across the lifetime of the project on this
    machine; if you copy the project to another machine, you'll get a
    new identity (because `.gitignore` keeps identity.json out of git).
    A future C-scope registry will reconcile by `(project_name,
    machine_id)`.
    """

    project_id: str
    machine_id: str
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class CurrentIteration:
    """In-flight review iteration state.

    `diff_blob_shas` is the deterministic fingerprint computed from
    `git diff --raw HEAD`, used for the `unchanged + previous_fail →
    needs_human` rule (ARCHITECTURE.md §2.3).
    """

    id: str
    started_at: datetime
    iteration_count: int
    max_iterations: int
    last_verdict: str | None = None
    diff_blob_shas: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class State:
    """Cache of orchestrator state.

    Use `dataclasses.replace` to derive new states; instances are frozen.
    """

    schema_version: int = SCHEMA_VERSION
    current_iteration: CurrentIteration | None = None


# ---------------------------------------------------------------------------
# Identity I/O
# ---------------------------------------------------------------------------


def load_identity(path: Path) -> Identity | None:
    """Read `identity.json` if present. Return None if missing.

    Raises:
        ValueError: if the file is malformed or has incompatible schema.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"identity.json is not valid JSON: {exc}") from exc

    schema = data.get("schema_version", 1)
    if schema > SCHEMA_VERSION:
        raise ValueError(
            f"identity.json schema_version={schema} is newer than "
            f"supported {SCHEMA_VERSION}"
        )
    return Identity(
        project_id=str(data["project_id"]),
        machine_id=str(data["machine_id"]),
        schema_version=int(schema),
    )


def save_identity(path: Path, identity: Identity) -> None:
    """Persist identity atomically. Creates parent dirs if needed."""
    _atomic_write_json(path, asdict(identity))


def init_identity(path: Path) -> Identity:
    """Load or create the project identity.

    First call generates a fresh UUID and writes the file. Subsequent
    calls return the existing identity unchanged.
    """
    existing = load_identity(path)
    if existing is not None:
        return existing
    identity = Identity(
        project_id=str(uuid.uuid4()),
        machine_id=str(uuid.uuid4()),
    )
    save_identity(path, identity)
    return identity


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def load_state(path: Path) -> State | None:
    """Read `state.json` if present. Return None if missing.

    A missing state file is not an error — callers reconstruct from
    audit log when needed.

    Raises:
        ValueError: if file exists but is malformed or has an
            incompatible schema.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"cannot read state.json: {exc}") from exc

    if not raw.strip():
        # An empty file is not a hard error; treat as no state.
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"state.json is not valid JSON: {exc}") from exc

    schema = data.get("schema_version", 1)
    if schema > SCHEMA_VERSION:
        raise ValueError(
            f"state.json schema_version={schema} is newer than "
            f"supported {SCHEMA_VERSION}"
        )

    iter_data = data.get("current_iteration")
    current = None
    if iter_data is not None:
        current = CurrentIteration(
            id=str(iter_data["id"]),
            started_at=datetime.fromisoformat(iter_data["started_at"]),
            iteration_count=int(iter_data["iteration_count"]),
            max_iterations=int(iter_data["max_iterations"]),
            last_verdict=iter_data.get("last_verdict"),
            diff_blob_shas=tuple(iter_data.get("diff_blob_shas", ())),
        )

    return State(schema_version=int(schema), current_iteration=current)


def save_state(path: Path, state: State) -> None:
    """Persist state atomically."""
    payload: dict[str, Any] = {
        "schema_version": state.schema_version,
    }
    if state.current_iteration is not None:
        ci = state.current_iteration
        payload["current_iteration"] = {
            "id": ci.id,
            "started_at": ci.started_at.isoformat(),
            "iteration_count": ci.iteration_count,
            "max_iterations": ci.max_iterations,
            "last_verdict": ci.last_verdict,
            "diff_blob_shas": list(ci.diff_blob_shas),
        }
    else:
        payload["current_iteration"] = None
    _atomic_write_json(path, payload)


def clear_iteration(path: Path) -> None:
    """Reset state to 'no active iteration' atomically.

    Convenience for the orchestrator on iteration-complete or error paths.
    """
    existing = load_state(path)
    schema = existing.schema_version if existing is not None else SCHEMA_VERSION
    save_state(path, State(schema_version=schema, current_iteration=None))


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write `payload` to `path` atomically.

    The temp file is created *in the same directory* as the target so
    `os.replace` is guaranteed to be atomic on Windows (cross-volume
    moves are not).

    On Windows, if the target is held open by a reader, `os.replace`
    may raise `PermissionError`. We retry once after a short delay,
    then re-raise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())

        try:
            os.replace(tmp_name, path)
        except PermissionError as exc:
            # Windows: target may be held open. One short retry, then bail.
            logger.warning(
                "atomic replace failed (%s), retrying once after 0.1s",
                exc,
            )
            time.sleep(0.1)
            os.replace(tmp_name, path)
    except Exception:
        # Ensure no orphan temp file remains.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _utc_now() -> datetime:
    return datetime.now(UTC)
