"""Integration tests for context_builder.

We exercise the real ``git`` binary against tiny tmp_path repos. This
keeps the tests honest about the contract with git (numstat output,
stash-create semantics, binary detection, initial-commit edge case).

What we test:

* Pre-flight: empty diff → ContextSkipped, large diff → ContextTooLarge,
  binary-only diff → ContextSkipped.
* Snapshot: git stash create captures a deterministic SHA; changed
  files are copied into ``.ccbridge/iteration-<id>/files/`` under
  forward-slash relative paths even on Windows.
* Prompt: rules, diff, recent audits all appear in the right
  sections; system prompt is included; cache_hit reflects whether
  the rules content matches the previously-recorded hash.
* Recent audits filter: only the current run_uuid's last 3 entries
  are included.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ccbridge.core.context_builder import (
    BuiltContext,
    ContextSkipped,
    ContextTooLargeError,
    build_context,
)
from ccbridge.core.events import IssueSummary, VerdictEvent

# ---------------------------------------------------------------------------
# Helpers — tiny git repo
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """Run git in `repo`; return stdout (text). Fails the test on non-zero."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    # Avoid CRLF surprises — store as-is.
    _git(repo, "config", "core.autocrlf", "false")


def _commit_file(repo: Path, rel_path: str, content: str, message: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    _git(repo, "commit", "-q", "-m", message)


def _modify_file(repo: Path, rel_path: str, content: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pre-flight: empty / binary / too-large
# ---------------------------------------------------------------------------


def test_empty_diff_yields_context_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")

    skipped = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(skipped, ContextSkipped)
    assert skipped.reason == "empty_diff"


def test_too_large_diff_raises(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")

    big = "\n".join(f"line_{i}" for i in range(5000)) + "\n"
    _modify_file(repo, "a.py", big)

    with pytest.raises(ContextTooLargeError) as exc_info:
        build_context(
            project_dir=repo,
            ccbridge_dir=repo / ".ccbridge",
            iteration_id="iter-1",
            run_uuid="run-1",
            rules_paths=(),
            max_diff_lines=2000,
        )
    assert exc_info.value.diff_lines >= 4999
    assert exc_info.value.limit == 2000


def test_binary_only_diff_yields_skipped(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "image.bin", "AAAA", "init")

    # Write actual binary content.
    (repo / "image.bin").write_bytes(b"\x00\x01\x02\x03" * 100)

    skipped = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(skipped, ContextSkipped)
    assert skipped.reason == "binary_only_diff"


# ---------------------------------------------------------------------------
# Initial-commit edge case
# ---------------------------------------------------------------------------


def test_works_with_uncommitted_initial_changes(tmp_path: Path) -> None:
    """Repo has init'd but no commits yet — build context from staged
    files. ARCHITECTURE.md §2.6 pre-flight #5.
    """
    repo = tmp_path / "proj"
    _init_repo(repo)

    # No HEAD yet: just untracked changes.
    (repo / "a.py").write_text("def f(): return 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")  # Staged but never committed.

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    assert "a.py" in built.diff_files


# ---------------------------------------------------------------------------
# Snapshot + path normalisation
# ---------------------------------------------------------------------------


def test_built_context_normalises_paths_to_forward_slash(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "src/app.py", "x = 1\n", "init")
    _modify_file(repo, "src/app.py", "x = 2\n")

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    assert "src/app.py" in built.diff_files
    # No backslashes in stored paths.
    assert all("\\" not in p for p in built.diff_files)


def test_snapshot_dir_contains_changed_files(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "src/app.py", "x = 1\n", "init")
    _modify_file(repo, "src/app.py", "x = 2\n# new line\n")

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-7",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    snap_file = built.snapshot_dir / "files" / "src" / "app.py"
    assert snap_file.exists()
    assert "new line" in snap_file.read_text(encoding="utf-8")


def test_file_line_counts_present_for_changed_files(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")
    _modify_file(repo, "a.py", "x = 1\ny = 2\nz = 3\n")

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    assert built.file_line_counts.get("a.py") == 3


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_prompt_contains_diff_section(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")
    _modify_file(repo, "a.py", "x = 2\n")

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    assert "## Diff" in built.prompt
    assert "a.py" in built.prompt


def test_prompt_includes_provided_rules(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")
    _modify_file(repo, "a.py", "x = 2\n")

    rules_dir = tmp_path / "rulebook"
    rules_dir.mkdir()
    (rules_dir / "R-001-test.md").write_text(
        "# R-001 test rule\n\nNo dummies.\n", encoding="utf-8"
    )

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(rules_dir / "R-001-test.md",),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    assert "R-001 test rule" in built.prompt
    assert "R-001" in built.known_rule_ids


def test_prompt_includes_recent_audits_only_for_current_run(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")
    _modify_file(repo, "a.py", "x = 2\n")

    older_run = VerdictEvent(
        run_uuid="other-run",
        iteration_id="iter-old",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        verdict="fail",
        summary="from another run — must NOT appear",
        issues=IssueSummary(),
        cost_usd=0.0,
        duration_sec=1.0,
        verdict_confidence=0.9,
        issues_completeness=0.9,
    )
    current_run = VerdictEvent(
        run_uuid="run-1",
        iteration_id="iter-1",
        ts=datetime(2026, 1, 2, tzinfo=UTC),
        verdict="fail",
        summary="UNIQUE_MARKER_FROM_THIS_RUN",
        issues=IssueSummary(major=1),
        cost_usd=0.01,
        duration_sec=2.0,
        verdict_confidence=0.85,
        issues_completeness=0.9,
    )

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-2",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
        recent_audits=[older_run, current_run],
    )
    assert isinstance(built, BuiltContext)
    assert "UNIQUE_MARKER_FROM_THIS_RUN" in built.prompt
    assert "from another run" not in built.prompt


def test_prompt_includes_system_prompt(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")
    _modify_file(repo, "a.py", "x = 2\n")

    built = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(),
        max_diff_lines=2000,
    )
    assert isinstance(built, BuiltContext)
    # We don't pin the exact wording, but the role marker must be there.
    assert "code reviewer" in built.prompt.lower()


# ---------------------------------------------------------------------------
# Cache hit signal (rules hash)
# ---------------------------------------------------------------------------


def test_cache_hit_false_on_first_call_true_on_second(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    _init_repo(repo)
    _commit_file(repo, "a.py", "x = 1\n", "init")
    _modify_file(repo, "a.py", "x = 2\n")

    rules_dir = tmp_path / "rulebook"
    rules_dir.mkdir()
    (rules_dir / "R-001.md").write_text("rule body\n", encoding="utf-8")

    first = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-1",
        run_uuid="run-1",
        rules_paths=(rules_dir / "R-001.md",),
        max_diff_lines=2000,
    )
    assert isinstance(first, BuiltContext)
    assert first.cache_hit is False

    # Modify the diff but not the rules → should hit the cache.
    _modify_file(repo, "a.py", "x = 3\n")
    second = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-2",
        run_uuid="run-1",
        rules_paths=(rules_dir / "R-001.md",),
        max_diff_lines=2000,
    )
    assert isinstance(second, BuiltContext)
    assert second.cache_hit is True

    # Now change the rule → cache miss again.
    (rules_dir / "R-001.md").write_text("rule body changed\n", encoding="utf-8")
    third = build_context(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        iteration_id="iter-3",
        run_uuid="run-1",
        rules_paths=(rules_dir / "R-001.md",),
        max_diff_lines=2000,
    )
    assert isinstance(third, BuiltContext)
    assert third.cache_hit is False
