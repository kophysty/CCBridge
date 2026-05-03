"""Shared run_audit invocation with config + identity propagation.

Both transports — CLI ``audit run`` and the Claude Code Stop hook —
need to read ``.ccbridge/config.toml`` and ``.ccbridge/identity.json``
and feed those into :func:`run_audit`. Without a single chokepoint
the two transports drifted: until 2026-05-03 (audit Major #2), both
called ``run_audit`` with just ``project_dir + ccbridge_dir + bus``,
silently dropping ``project_id``, ``project_name``, ``rules_paths``,
``max_iterations``, ``max_diff_lines``. config.toml was decorative.

This module is the chokepoint. CLI and Stop hook both call
:func:`run_audit_with_config`.

Error policy is parameterised by ``cli_mode``:

* ``cli_mode=True`` — surface malformed config errors to the user
  via exception (CLI exits non-zero with a clear message).
* ``cli_mode=False`` — Stop hook discipline: never wedge Claude on
  a user's TOML typo. Report on stderr and fall back to defaults.

Path resolution helpers (``resolve_include_rules``, ``is_inside``)
live here too because they are part of the same "config → run_audit"
flow.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from ccbridge.core.event_bus import EventBus
from ccbridge.core.orchestrator import OrchestratorOutcome, run_audit
from ccbridge.core.state import init_identity


def run_audit_with_config(
    *,
    project_dir: Path,
    ccbridge_dir: Path,
    bus: EventBus,
    cli_mode: bool,
    run_uuid: str | None = None,
) -> OrchestratorOutcome:
    """Read config + identity, invoke run_audit with everything wired."""
    # Late imports to avoid pulling in load_config in test environments
    # where it isn't strictly needed.
    from ccbridge.core.config import Config, load_config

    try:
        config = load_config(project_dir)
    except ValueError as exc:
        if cli_mode:
            raise
        _stderr(
            f"ccbridge: config.toml is malformed ({exc}); "
            f"falling back to defaults"
        )
        config = Config()

    # init_identity is idempotent: returns existing or generates new.
    # Handles the "user ran audit run without init first" path.
    identity = init_identity(ccbridge_dir / "identity.json")

    rules_paths = resolve_include_rules(
        project_dir, config.review.include_rules
    )

    return run_audit(
        project_dir=project_dir,
        ccbridge_dir=ccbridge_dir,
        bus=bus,
        run_uuid=run_uuid or str(uuid.uuid4()),
        project_id=identity.project_id,
        project_name=config.project.name,
        rules_paths=rules_paths,
        max_iterations=config.review.max_iterations,
        max_diff_lines=config.review.max_diff_lines,
    )


def resolve_include_rules(
    project_dir: Path, patterns: tuple[str, ...]
) -> tuple[Path, ...]:
    """Resolve config.toml ``[review] include_rules`` to absolute file paths.

    Auto-detect literal vs glob: a pattern is treated as a glob if it
    contains any of ``*``, ``?``, ``[``. Otherwise it's a literal path
    relative to ``project_dir``.

    Edge cases (per audit feedback 2026-05-03):

    - Glob with no matches: silently empty.
    - Literal missing: stderr warning, skip.
    - Path resolving outside ``project_dir``: stderr warning, skip
      (defensive against accidental traversal via ``../``).
    - Literal pointing to a directory: stderr warning, skip.
    - Glob pattern matching a directory: silently skip.
    - Result is deduplicated, order-preserving.

    All warnings go to stderr; stdout is reserved for the Stop hook
    decision JSON.
    """
    project_root = project_dir.resolve()
    seen: dict[Path, None] = {}

    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue

        is_glob = any(ch in pattern for ch in "*?[")

        if is_glob:
            try:
                matches = sorted(project_dir.glob(pattern))
            except (OSError, ValueError) as exc:
                _stderr(f"include_rules: glob error for {pattern!r}: {exc}")
                continue
            for candidate in matches:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=False)
                if not is_inside(resolved, project_root):
                    _stderr(
                        f"include_rules: glob match {resolved} is "
                        f"outside project root, skipped"
                    )
                    continue
                seen.setdefault(resolved, None)
        else:
            candidate = project_dir / pattern
            resolved = candidate.resolve(strict=False)
            if not is_inside(resolved, project_root):
                _stderr(
                    f"include_rules: {pattern!r} resolves outside "
                    f"project root ({resolved}); skipped"
                )
                continue
            if not resolved.exists():
                _stderr(
                    f"include_rules: {pattern!r} not found at "
                    f"{resolved}; skipped"
                )
                continue
            if not resolved.is_file():
                _stderr(
                    f"include_rules: {pattern!r} is a directory at "
                    f"{resolved}, expected a file; skipped"
                )
                continue
            seen.setdefault(resolved, None)

    return tuple(seen.keys())


def is_inside(path: Path, root: Path) -> bool:
    """True if ``path`` (already resolved) is inside or equal to ``root``."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


__all__ = (
    "is_inside",
    "resolve_include_rules",
    "run_audit_with_config",
)
