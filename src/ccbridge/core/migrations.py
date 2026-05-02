"""Schema migration runtime for CCBridge state files.

CCBridge state lives in three places:

* `state.json` — runtime cache (see `core.state`)
* `identity.json` — per-machine identity (see `core.state`)
* `audit.jsonl` — primary log (see `core.audit_log`)

Each carries `schema_version`. When CCBridge upgrades and a file format
changes, this module applies the appropriate `v1 → v2 → ...` chain.

Backward compatibility (reading old data with new code):
    Add a forward migration. Old files always upgrade on read.

Forward compatibility (reading new data with old code):
    Pydantic uses `extra="ignore"`, so unknown fields are dropped
    silently. Unknown event types in audit.jsonl are skipped with a
    warning by the tolerant reader. Newer `schema_version` raises
    ValueError in `load_state`/`load_identity` — that's intentional,
    a downgrade should not silently lose data.

In v0.1 there is exactly one schema version (1), so this module is
mostly a stub; the public API is set up to make adding v2 a one-line
registration in `_MIGRATIONS`.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------

# Each entry: (from_version, to_version) → callable(data: dict) -> dict.
# Applied in order starting from the file's current version up to CURRENT.
_MIGRATIONS: dict[tuple[int, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_migration(
    from_version: int,
    to_version: int,
) -> Callable[
    [Callable[[dict[str, Any]], dict[str, Any]]],
    Callable[[dict[str, Any]], dict[str, Any]],
]:
    """Decorator-style registration of a migration step.

    Usage::

        @register_migration(1, 2)
        def _v1_to_v2(data: dict) -> dict:
            data["new_field"] = "default"
            return data
    """

    def decorator(
        fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        _MIGRATIONS[(from_version, to_version)] = fn
        return fn

    return decorator


def migrate(data: dict[str, Any], target_version: int = CURRENT_SCHEMA_VERSION) -> dict[str, Any]:
    """Upgrade `data` from its declared `schema_version` to `target_version`.

    Args:
        data: A mutable dict with at least `schema_version` set. If the
            field is absent, version is treated as 1.
        target_version: Desired output version. Defaults to CURRENT.

    Raises:
        UnknownSchemaError: Cannot find a migration step for some
            intermediate hop.
    """
    current = int(data.get("schema_version", 1))
    if current == target_version:
        return data
    if current > target_version:
        raise UnknownSchemaError(
            f"data is at version {current}, cannot downgrade to {target_version}"
        )

    while current < target_version:
        next_version = current + 1
        step = _MIGRATIONS.get((current, next_version))
        if step is None:
            raise UnknownSchemaError(
                f"no migration registered for {current} → {next_version}"
            )
        logger.info("migrating data: v%d → v%d", current, next_version)
        data = step(data)
        data["schema_version"] = next_version
        current = next_version

    return data


class UnknownSchemaError(RuntimeError):
    """Raised when no migration path exists from current to target version."""


# ---------------------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------------------


def backup_file(path: Path, suffix: str = ".bak") -> Path:
    """Copy `path` to `path.with_suffix(... + suffix)` and return the new path.

    Used when a file format is too old or unrecognised to migrate
    automatically — we preserve the original before any destructive
    action so the user can recover.

    Returns the path of the backup. If the source doesn't exist, returns
    a non-existent path without error (caller may rely on idempotence).
    """
    if not path.exists():
        return path.with_suffix(path.suffix + suffix)

    target = path.with_suffix(path.suffix + suffix)
    n = 1
    while target.exists():
        target = path.with_suffix(f"{path.suffix}{suffix}.{n}")
        n += 1

    shutil.copy2(path, target)
    logger.warning("backed up %s → %s", path, target)
    return target
