"""Build the prompt and snapshot that gets sent to Codex for one iteration.

The orchestrator hands us a project directory, an iteration id, the
rules to enforce, and the recent audit events of the current run. We
return a :class:`BuiltContext` that contains everything Codex needs:

* A prompt assembled from the system role, the rules (cacheable), the
  diff, and the recent audits (uncacheable, current run only).
* A *snapshot* of the current diff under
  ``.ccbridge/iteration-<id>/files/`` so that any further edits the
  developer makes do not affect the in-flight review (ARCHITECTURE.md
  §2.6 "Diff snapshot", AC-20).
* Metadata the orchestrator needs for downstream semantic validation:
  ``diff_files``, ``file_line_counts``, ``known_rule_ids``.

Pre-flight short-circuits (ARCHITECTURE.md §2.6, AC-14, AC-18):

* Empty diff → :class:`ContextSkipped` (``reason="empty_diff"``).
* Binary-only diff → :class:`ContextSkipped` (``reason="binary_only_diff"``).
* Diff > ``max_diff_lines`` → :class:`ContextTooLargeError`.

Path normalisation: every path stored in ``BuiltContext`` uses forward
slashes, even on Windows. The orchestrator and verdict semantic
validator agree on this convention (ARCHITECTURE.md §2.8).
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ccbridge.core.events import VerdictEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_RECENT_AUDITS = 3
RULE_ID_RE = re.compile(r"\bR-\d{3,}\b")

SYSTEM_PROMPT = (
    "You are a senior code reviewer. You receive a code diff, the "
    "changed files, project rules, and recent review history. You "
    "return a single JSON object matching the Verdict schema. "
    "No prose, no markdown. Do not invent issues. Do not echo prior "
    "audits. critical/major issues forbid verdict=pass."
)

INSTRUCTION_TAIL = (
    "Now produce Verdict JSON. Reminder: rules_checked must list "
    "EVERY rule_id from above. critical/major → verdict ≠ pass."
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltContext:
    """Everything Codex needs for one review iteration."""

    prompt: str
    diff_files: tuple[str, ...]
    file_line_counts: dict[str, int]
    known_rule_ids: tuple[str, ...]
    diff_lines: int
    snapshot_dir: Path
    snapshot_sha: str | None
    cache_hit: bool
    rules_count: int


@dataclass(frozen=True)
class ContextSkipped:
    """Returned when there is nothing for Codex to review.

    The orchestrator should record a verdict=skipped audit entry and
    exit cleanly.
    """

    reason: str
    detail: str = ""


class ContextTooLargeError(RuntimeError):
    """Raised when the diff exceeds ``max_diff_lines``.

    Carries the actual line count and the configured limit so the CLI
    can produce a helpful message ("Diff too large (5000 > 2000); use
    --force or --per-file").
    """

    def __init__(self, diff_lines: int, limit: int) -> None:
        self.diff_lines = diff_lines
        self.limit = limit
        super().__init__(
            f"diff has {diff_lines} lines, exceeds limit {limit}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context(
    *,
    project_dir: Path,
    ccbridge_dir: Path,
    iteration_id: str,
    run_uuid: str,
    rules_paths: tuple[Path, ...] = (),
    max_diff_lines: int = 2000,
    min_diff_lines: int = 0,
    recent_audits: list[VerdictEvent] | None = None,
    recent_audits_limit: int = DEFAULT_RECENT_AUDITS,
) -> BuiltContext | ContextSkipped:
    """Assemble the per-iteration context for Codex.

    Returns
    -------
    BuiltContext
        Normal case — proceed with the review.
    ContextSkipped
        The diff is empty or binary-only. Orchestrator should write a
        ``verdict=skipped`` audit entry and finish the run.

    Raises
    ------
    ContextTooLargeError
        Diff exceeds the configured cap.
    subprocess.CalledProcessError
        ``git`` failed in an unexpected way (broken repo, missing
        binary). The orchestrator should treat this as an operational
        error and surface it via :class:`ErrorEvent`.
    """
    numstat = _git_numstat(project_dir)
    if not numstat:
        return ContextSkipped(reason="empty_diff")

    if all(item.is_binary for item in numstat):
        return ContextSkipped(
            reason="binary_only_diff",
            detail=f"{len(numstat)} binary file(s)",
        )

    text_items = [item for item in numstat if not item.is_binary]
    diff_lines = sum(item.added + item.deleted for item in text_items)
    if diff_lines > max_diff_lines:
        raise ContextTooLargeError(diff_lines=diff_lines, limit=max_diff_lines)

    # Trivial-diff skip: if the diff is at or below the configured
    # threshold, short-circuit before invoking Codex. ``min_diff_lines=0``
    # disables this entirely (default).
    if min_diff_lines > 0 and diff_lines <= min_diff_lines:
        return ContextSkipped(
            reason="trivial_diff",
            detail=f"{diff_lines} changed line(s) ≤ threshold {min_diff_lines}",
        )

    diff_text = _git_diff(project_dir)
    snapshot_sha = _git_stash_create(project_dir)

    iteration_dir = ccbridge_dir / f"iteration-{iteration_id}"
    files_dir = iteration_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (iteration_dir / "diff.patch").write_text(diff_text, encoding="utf-8")

    diff_files: list[str] = []
    file_line_counts: dict[str, int] = {}
    for item in text_items:
        rel = _normalise_path(item.path)
        diff_files.append(rel)
        src = project_dir / item.path
        if src.exists() and src.is_file():
            try:
                content = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Mid-edit binary or unusual encoding: skip the copy
                # but keep the diff entry so semantic validation still
                # rejects line numbers.
                continue
            dest = files_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            file_line_counts[rel] = content.count("\n") + (
                0 if content.endswith("\n") or not content else 1
            )

    rules_text, rules_hash = _read_rules(rules_paths)
    known_rule_ids = _extract_rule_ids(rules_paths)

    cache_marker = ccbridge_dir / "rules-cache.sha256"
    cache_hit = _check_cache_hit(cache_marker, rules_hash)
    cache_marker.parent.mkdir(parents=True, exist_ok=True)
    cache_marker.write_text(rules_hash, encoding="utf-8")

    audits = list(recent_audits or [])
    audits = [a for a in audits if a.run_uuid == run_uuid][-recent_audits_limit:]

    prompt = _assemble_prompt(
        rules_text=rules_text,
        diff_text=diff_text,
        diff_files=tuple(diff_files),
        recent_audits=audits,
    )

    return BuiltContext(
        prompt=prompt,
        diff_files=tuple(diff_files),
        file_line_counts=file_line_counts,
        known_rule_ids=known_rule_ids,
        diff_lines=diff_lines,
        snapshot_dir=iteration_dir,
        snapshot_sha=snapshot_sha,
        cache_hit=cache_hit,
        rules_count=len(rules_paths),
    )


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _NumstatItem:
    added: int
    deleted: int
    is_binary: bool
    path: str


def _git_numstat(project_dir: Path) -> list[_NumstatItem]:
    """Return ``git diff --numstat HEAD`` parsed.

    Falls back to ``git diff --numstat --cached`` when HEAD does not
    exist (the very first commit hasn't been made yet — see
    ARCHITECTURE.md §2.6 pre-flight #5).
    """
    proc = subprocess.run(
        ["git", "diff", "--numstat", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 and "unknown revision" in (proc.stderr or "").lower():
        proc = subprocess.run(
            ["git", "diff", "--numstat", "--cached"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    elif proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, proc.stdout, proc.stderr
        )

    items: list[_NumstatItem] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added_s, deleted_s, path = parts
        is_binary = added_s == "-" and deleted_s == "-"
        added = 0 if is_binary else int(added_s)
        deleted = 0 if is_binary else int(deleted_s)
        items.append(_NumstatItem(added, deleted, is_binary, path))
    return items


def _git_diff(project_dir: Path) -> str:
    """Full text diff of the working tree against HEAD (or staged when
    HEAD is missing).
    """
    proc = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 and "unknown revision" in (proc.stderr or "").lower():
        proc = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    elif proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, proc.stdout, proc.stderr
        )
    return proc.stdout


def _git_stash_create(project_dir: Path) -> str | None:
    """``git stash create`` produces a deterministic snapshot SHA without
    touching the working tree. Returns None if there's nothing to stash
    (e.g. only staged changes pre-initial-commit).
    """
    proc = subprocess.run(
        ["git", "stash", "create"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _read_rules(rules_paths: tuple[Path, ...]) -> tuple[str, str]:
    """Return concatenated rule text + a stable SHA-256 of the bundle.

    The SHA covers (path, content) pairs so that renaming a rule
    invalidates the cache even if the body is identical.
    """
    if not rules_paths:
        return "", hashlib.sha256(b"").hexdigest()

    parts: list[str] = []
    hasher = hashlib.sha256()
    for path in rules_paths:
        text = path.read_text(encoding="utf-8")
        parts.append(f"### {path.name}\n\n{text}")
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(text.encode("utf-8"))
        hasher.update(b"\x00")
    return "\n\n".join(parts), hasher.hexdigest()


def _extract_rule_ids(rules_paths: tuple[Path, ...]) -> tuple[str, ...]:
    """Pull every ``R-NNN`` token out of the rules bundle for the
    semantic validator's ``known_rule_ids`` set.

    Returns a deduplicated, order-preserving tuple.
    """
    seen: dict[str, None] = {}
    for path in rules_paths:
        text = path.read_text(encoding="utf-8")
        for match in RULE_ID_RE.finditer(text):
            seen.setdefault(match.group(0), None)
        for match in RULE_ID_RE.finditer(path.name):
            seen.setdefault(match.group(0), None)
    return tuple(seen.keys())


def _check_cache_hit(marker_path: Path, current_hash: str) -> bool:
    if not marker_path.exists():
        return False
    try:
        previous = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return previous == current_hash


# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------


def _normalise_path(raw: str) -> str:
    return raw.replace("\\", "/")


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _assemble_prompt(
    *,
    rules_text: str,
    diff_text: str,
    diff_files: tuple[str, ...],
    recent_audits: list[VerdictEvent],
) -> str:
    sections: list[str] = []
    sections.append(f"# System\n\n{SYSTEM_PROMPT}")

    if rules_text:
        sections.append(f"# Rules (cacheable)\n\n{rules_text}")

    if diff_files:
        files_listing = "\n".join(f"- {p}" for p in diff_files)
        sections.append(f"# Files in diff\n\n{files_listing}")

    sections.append(f"## Diff\n\n```diff\n{diff_text}\n```")

    if recent_audits:
        rendered = "\n\n".join(_render_audit(a) for a in recent_audits)
        sections.append(f"# Recent audits (this run)\n\n{rendered}")

    sections.append(INSTRUCTION_TAIL)
    return "\n\n".join(sections)


def _render_audit(event: VerdictEvent) -> str:
    return (
        f"- iter={event.iteration_id} verdict={event.verdict} "
        f"confidence={event.verdict_confidence:.2f} "
        f"summary={event.summary}"
    )


# ---------------------------------------------------------------------------
# Cleanup helper (called by orchestrator after a successful iteration)
# ---------------------------------------------------------------------------


def cleanup_iteration(snapshot_dir: Path) -> None:
    """Remove a per-iteration snapshot directory after the verdict
    has been recorded. Idempotent.
    """
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir, ignore_errors=True)


# Re-export to keep the module's `dataclass` reference around for tools
# that introspect frozen dataclasses (mypy, ruff strict).
__all__ = (
    "BuiltContext",
    "ContextSkipped",
    "ContextTooLargeError",
    "build_context",
    "cleanup_iteration",
)
