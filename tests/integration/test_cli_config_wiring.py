"""Audit Major #2 (2026-05-03): config.toml + identity.json must be
read and propagated into run_audit by both transports (CLI audit run
and Stop hook).

Before this fix:
- StartedEvent.project_id was empty string (identity ignored)
- StartedEvent.project_name was "untitled" (config.toml [project]
  name ignored)
- ContextBuiltEvent.rules_count was 0 (config.toml [review]
  include_rules ignored)
- max_iterations / max_diff_lines hardcoded to defaults (config
  values ignored)

These tests assert that the values flow from .ccbridge/config.toml
and .ccbridge/identity.json into the audit events that orchestrator
emits.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ccbridge.cli import _resolve_include_rules, cli
from ccbridge.core.audit_log import AuditLog
from ccbridge.core.events import (
    ContextBuiltEvent,
    StartedEvent,
)
from ccbridge.runners.codex_runner import CodexRunResult

# ---------------------------------------------------------------------------
# Helpers — git repo with diff + initialized .ccbridge/
# ---------------------------------------------------------------------------


def _make_initialized_repo(
    tmp_path: Path,
    *,
    config_toml: str = "",
    rule_files: dict[str, str] | None = None,
) -> Path:
    """Create a tmp_path git repo, run `ccbridge init`, optionally
    overwrite config.toml, optionally seed rule files, leave one diff
    so audit run has work to do.
    """
    repo = tmp_path / "proj"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "core.autocrlf", "false"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(repo)])

    if config_toml:
        (repo / ".ccbridge" / "config.toml").write_text(
            config_toml, encoding="utf-8"
        )

    if rule_files:
        for rel_path, content in rule_files.items():
            target = repo / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    # Make a diff for the audit run.
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    return repo


def _stub_codex(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    def fake(*, prompt: str, cwd: Path, **kwargs: Any) -> CodexRunResult:
        return CodexRunResult(
            parsed=payload,
            stdout=json.dumps(payload),
            stderr="",
            returncode=0,
            retry_count=0,
        )

    monkeypatch.setattr("ccbridge.core.orchestrator.run_codex", fake)


def _verdict_pass() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "verdict": "pass",
        "summary": "ok",
        "issues": [],
        "verdict_confidence": 0.9,
        "issues_completeness": 0.9,
        "files_reviewed": ["a.py"],
        "rules_checked": ["R-001"],
    }


def _read_events(repo: Path) -> list[Any]:
    audit_path = repo / ".ccbridge" / "audit.jsonl"
    return list(AuditLog(audit_path).read_all())


# ---------------------------------------------------------------------------
# project_id propagation (closes AC-7 properly)
# ---------------------------------------------------------------------------


def test_audit_run_propagates_project_id_from_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_initialized_repo(tmp_path)
    identity_path = repo / ".ccbridge" / "identity.json"
    identity_data = json.loads(identity_path.read_text(encoding="utf-8"))
    expected_pid = identity_data["project_id"]

    _stub_codex(monkeypatch, _verdict_pass())

    runner = CliRunner()
    runner.invoke(cli, ["audit", "run", "--project", str(repo), "--json"])

    events = _read_events(repo)
    starts = [e for e in events if isinstance(e, StartedEvent)]
    assert starts, "no StartedEvent in audit.jsonl"
    assert starts[0].project_id == expected_pid


def test_audit_run_creates_identity_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If user runs `audit run` without `init` first, run_audit must
    create identity (init_identity is idempotent — closes the
    "user skipped init" path).
    """
    repo = tmp_path / "no-init-proj"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "core.autocrlf", "false"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")

    _stub_codex(monkeypatch, _verdict_pass())

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "run", "--project", str(repo), "--json"]
    )
    assert result.exit_code == 0

    identity_path = repo / ".ccbridge" / "identity.json"
    assert identity_path.exists()
    events = _read_events(repo)
    starts = [e for e in events if isinstance(e, StartedEvent)]
    assert starts and starts[0].project_id


# ---------------------------------------------------------------------------
# project_name + max_iterations + max_diff_lines from config.toml
# ---------------------------------------------------------------------------


def test_audit_run_propagates_project_name_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = """
[project]
name = "oil-automation"

[review]
context_level = "medium"
max_iterations = 3
max_diff_lines = 2000
include_rules = []
"""
    repo = _make_initialized_repo(tmp_path, config_toml=config)
    _stub_codex(monkeypatch, _verdict_pass())

    runner = CliRunner()
    runner.invoke(cli, ["audit", "run", "--project", str(repo), "--json"])

    events = _read_events(repo)
    starts = [e for e in events if isinstance(e, StartedEvent)]
    assert starts and starts[0].project_name == "oil-automation"


def test_audit_run_uses_max_iterations_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If config sets max_iterations=2, three fail payloads should be
    capped at iteration 2 → needs_human (not 3 like the default).
    """
    config = """
[project]
name = "p"

[review]
context_level = "medium"
max_iterations = 2
max_diff_lines = 2000
include_rules = []
"""
    repo = _make_initialized_repo(tmp_path, config_toml=config)
    fail_payload = {
        "schema_version": 1,
        "verdict": "fail",
        "summary": "issues",
        "issues": [
            {
                "severity": "major",
                "category": "correctness",
                "file": "a.py",
                "line": 1,
                "message": "broken",
                "rule_id": "R-001",
            }
        ],
        "verdict_confidence": 0.85,
        "issues_completeness": 0.9,
        "files_reviewed": ["a.py"],
        "rules_checked": ["R-001"],
    }
    _stub_codex(monkeypatch, fail_payload)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "run", "--project", str(repo), "--json"]
    )
    outcome = json.loads(result.stdout)

    # max=2 should escalate after 2 fails, not 3.
    assert outcome["iterations_used"] == 2
    assert outcome["final_verdict"] == "needs_human"


def test_audit_run_skips_when_diff_below_skip_trivial_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: with skip_trivial_diff_max_lines=5 in config and a
    2-line diff, audit run skips Codex entirely and the JSON outcome
    reports final_verdict=skipped.
    """
    config = """
[project]
name = "p"

[review]
skip_trivial_diff_max_lines = 5
"""
    repo = _make_initialized_repo(tmp_path, config_toml=config)

    # The repo has one 2-line change (x=1 → x=2 in a.py).
    # Codex must NEVER be called.
    def must_not_call_codex(*args: Any, **kwargs: Any) -> CodexRunResult:
        raise AssertionError("Codex should not be invoked when diff is below threshold")

    monkeypatch.setattr(
        "ccbridge.core.orchestrator.run_codex", must_not_call_codex
    )

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "run", "--project", str(repo), "--json"]
    )
    assert result.exit_code == 0, result.stdout
    outcome = json.loads(result.stdout)
    assert outcome["final_verdict"] == "skipped"


# ---------------------------------------------------------------------------
# include_rules — auto-detect literal/glob
# ---------------------------------------------------------------------------


def test_audit_run_resolves_literal_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = """
[project]
name = "p"

[review]
include_rules = ["Rulebook/R-001-foo.md", "CLAUDE.md"]
"""
    repo = _make_initialized_repo(
        tmp_path,
        config_toml=config,
        rule_files={
            "Rulebook/R-001-foo.md": "# R-001 rule\nNo dummies.\n",
            "CLAUDE.md": "# Project guide\n",
        },
    )
    _stub_codex(monkeypatch, _verdict_pass())

    runner = CliRunner()
    runner.invoke(cli, ["audit", "run", "--project", str(repo), "--json"])

    events = _read_events(repo)
    ctx_built = [e for e in events if isinstance(e, ContextBuiltEvent)]
    assert ctx_built and ctx_built[0].rules_count == 2


def test_audit_run_resolves_glob_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = """
[project]
name = "p"

[review]
include_rules = ["Rulebook/R-*.md"]
"""
    repo = _make_initialized_repo(
        tmp_path,
        config_toml=config,
        rule_files={
            "Rulebook/R-001-foo.md": "rule 1",
            "Rulebook/R-002-bar.md": "rule 2",
            "Rulebook/notes.md": "not a rule",
        },
    )
    _stub_codex(monkeypatch, _verdict_pass())

    runner = CliRunner()
    runner.invoke(cli, ["audit", "run", "--project", str(repo), "--json"])

    events = _read_events(repo)
    ctx_built = [e for e in events if isinstance(e, ContextBuiltEvent)]
    # 2 R-*.md files matched; notes.md excluded.
    assert ctx_built and ctx_built[0].rules_count == 2


# ---------------------------------------------------------------------------
# _resolve_include_rules — direct unit-style tests for edge cases
# ---------------------------------------------------------------------------


class TestResolveIncludeRules:
    def test_empty_returns_empty(self, tmp_path: Path) -> None:
        result = _resolve_include_rules(tmp_path, ())
        assert result == ()

    def test_glob_no_matches_returns_empty_no_error(
        self, tmp_path: Path
    ) -> None:
        result = _resolve_include_rules(tmp_path, ("Rulebook/R-*.md",))
        assert result == ()

    def test_literal_missing_skipped_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        result = _resolve_include_rules(
            tmp_path, ("Rulebook/R-missing.md",)
        )
        assert result == ()
        captured = capsys.readouterr()
        # Warning to stderr, NOT stdout (Stop hook discipline).
        assert captured.out == ""
        assert "missing" in captured.err.lower() or "not found" in captured.err.lower()

    def test_literal_outside_project_dir_rejected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Create a file in a sibling dir to tmp_path/project — outside.
        outside = tmp_path / "other"
        outside.mkdir()
        (outside / "rule.md").write_text("evil", encoding="utf-8")
        project = tmp_path / "project"
        project.mkdir()

        result = _resolve_include_rules(
            project, ("../other/rule.md",)
        )
        assert result == ()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            "outside" in captured.err.lower()
            or "escape" in captured.err.lower()
            or "project" in captured.err.lower()
        )

    def test_literal_directory_skipped_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "Rulebook").mkdir()
        result = _resolve_include_rules(tmp_path, ("Rulebook",))
        assert result == ()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            "directory" in captured.err.lower()
            or "not a file" in captured.err.lower()
        )

    def test_glob_matching_directory_silently_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Per audit feedback: directories matched by a glob are
        silently skipped (warning only for literals — globs already
        imply "match what you can").
        """
        (tmp_path / "Rulebook").mkdir()
        (tmp_path / "Rulebook" / "R-001.md").write_text("ok", encoding="utf-8")
        # `Rulebook/*` would match both the file AND any subdir entry.
        result = _resolve_include_rules(tmp_path, ("Rulebook/*",))
        assert len(result) == 1  # only the file, dir silently skipped
        # No warning for the directory match.
        captured = capsys.readouterr()
        # Allow no warnings at all (the file is fine).
        assert "directory" not in captured.err.lower() or captured.err == ""

    def test_deduplicate_preserves_order(self, tmp_path: Path) -> None:
        (tmp_path / "Rulebook").mkdir()
        (tmp_path / "Rulebook" / "R-001.md").write_text("a", encoding="utf-8")
        (tmp_path / "Rulebook" / "R-002.md").write_text("b", encoding="utf-8")

        # First pattern matches both via glob; second pattern is literal
        # for one of them. Should not appear twice.
        result = _resolve_include_rules(
            tmp_path,
            ("Rulebook/R-*.md", "Rulebook/R-001.md"),
        )
        # 2 unique files.
        assert len(result) == 2
        # Order: glob expands deterministically by Path.glob (sorted by
        # filesystem), R-001 first; literal R-001 already seen, dedup'd.
        names = [p.name for p in result]
        assert names == ["R-001.md", "R-002.md"]

    def test_returns_paths_only_files(self, tmp_path: Path) -> None:
        (tmp_path / "Rulebook").mkdir()
        rule_file = tmp_path / "Rulebook" / "R.md"
        rule_file.write_text("x", encoding="utf-8")

        result = _resolve_include_rules(tmp_path, ("Rulebook/R.md",))
        assert all(isinstance(p, Path) for p in result)
        assert all(p.is_file() for p in result)
