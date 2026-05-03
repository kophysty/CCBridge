"""Integration tests for the ccbridge CLI (PR2b step 6a).

Read/run-only commands:

* ``ccbridge audit run`` — invoke orchestrator, render events live,
  print final outcome summary.
* ``ccbridge audit list`` — show history of runs from audit.jsonl.
* ``ccbridge audit get [RUN_UUID]`` — show details of one run.
* ``ccbridge audit watch`` — tail audit.jsonl into RichRenderer.
* ``ccbridge status`` — minimal read-only state summary.

Output format contract:

* Default: human (rich/table/plain) on stdout, diagnostics on stderr.
* ``--json``: stdout is STRICTLY valid JSON. No rich/ANSI/text.
  Diagnostics still go to stderr only.

Project resolution (per audit feedback):

1. ``--project PATH`` is the override.
2. ``git rev-parse --show-toplevel`` from cwd.
3. Fallback to cwd.

Invariant: ``init`` / ``uninstall`` are deferred to PR2b step 6b.
6a never patches user files — strictly read/run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ccbridge.cli import cli
from ccbridge.core.audit_log import AuditLog
from ccbridge.core.events import (
    IssueSummary,
    IterationCompleteEvent,
    StartedEvent,
    VerdictEvent,
)
from ccbridge.runners.codex_runner import CodexRunResult

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
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
        ["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
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


def _verdict_payload(verdict: str = "pass") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "verdict": verdict,
        "summary": f"summary for {verdict}",
        "issues": [],
        "verdict_confidence": 0.9,
        "issues_completeness": 0.9,
        "files_reviewed": ["a.py"],
        "rules_checked": ["R-001"],
    }


def _populate_audit_log(repo: Path, run_uuid: str, verdict: str) -> None:
    """Write a synthetic run history into audit.jsonl for list/get tests."""
    audit_path = repo / ".ccbridge" / "audit.jsonl"
    log = AuditLog(audit_path)
    log.append(
        StartedEvent(
            run_uuid=run_uuid,
            iteration_id=f"{run_uuid}-1",
            project_name="proj",
            project_id="pid",
            iteration_count=1,
            max_iterations=3,
        )
    )
    log.append(
        VerdictEvent(
            run_uuid=run_uuid,
            iteration_id=f"{run_uuid}-1",
            verdict=verdict,
            summary=f"summary {run_uuid[:6]}",
            issues=IssueSummary(),
            cost_usd=0.05,
            duration_sec=2.0,
            verdict_confidence=0.9,
            issues_completeness=0.9,
        )
    )
    log.append(
        IterationCompleteEvent(
            run_uuid=run_uuid,
            iteration_id=f"{run_uuid}-1",
            final_verdict=verdict,
            iterations_used=1,
            total_cost_usd=0.05,
            duration_sec=2.0,
        )
    )


def _assert_pure_json(stdout: str) -> Any:
    """In --json mode, stdout must be a single JSON document, no extras."""
    data = json.loads(stdout)
    return data


# ---------------------------------------------------------------------------
# `ccbridge audit run`
# ---------------------------------------------------------------------------


def test_audit_run_invokes_orchestrator_with_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    _stub_codex(monkeypatch, _verdict_payload("pass"))

    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "run", "--project", str(repo)])

    assert result.exit_code == 0
    # human mode → some rich output to stdout (not strict format).
    assert "pass" in result.output.lower()
    # audit.jsonl populated.
    audit = AuditLog(repo / ".ccbridge" / "audit.jsonl")
    events = list(audit.read_all())
    assert any(isinstance(e, VerdictEvent) for e in events)


def test_audit_run_json_outputs_only_outcome_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--json: stdout is OrchestratorOutcome serialised, nothing else.
    Live event stream goes nowhere (or stderr); user can read full
    history in audit.jsonl.

    NB: 3 fail payloads → orchestrator escalates to needs_human per
    AC-3, that's the correct contract — what we test here is the
    JSON shape, not which terminal verdict we land on.
    """
    repo = _make_repo(tmp_path)
    _stub_codex(monkeypatch, _verdict_payload("pass"))

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "run", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    assert isinstance(data, dict)
    assert data.get("final_verdict") == "pass"
    assert "run_uuid" in data
    assert "iterations_used" in data
    # No ANSI in stdout.
    assert "\x1b[" not in result.stdout


def test_audit_run_uses_git_toplevel_when_no_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run from a subdir of a git repo: --project absent → uses
    git rev-parse --show-toplevel. .ccbridge/ должен лечь в root,
    не в subdir.
    """
    repo = _make_repo(tmp_path)
    subdir = repo / "src"
    subdir.mkdir(exist_ok=True)
    _stub_codex(monkeypatch, _verdict_payload("pass"))

    runner = CliRunner()
    # Invoke from the subdir.
    monkeypatch.chdir(subdir)
    result = runner.invoke(cli, ["audit", "run"])

    assert result.exit_code == 0
    assert (repo / ".ccbridge" / "audit.jsonl").exists()
    assert not (subdir / ".ccbridge").exists()


def test_audit_run_explicit_project_overrides_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _stub_codex(monkeypatch, _verdict_payload("pass"))

    # cwd is `other` (not in any repo); --project points to `repo`.
    monkeypatch.chdir(other)
    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "run", "--project", str(repo)])

    assert result.exit_code == 0
    assert (repo / ".ccbridge" / "audit.jsonl").exists()


# ---------------------------------------------------------------------------
# `ccbridge audit list`
# ---------------------------------------------------------------------------


def test_audit_list_human_shows_runs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")
    _populate_audit_log(repo, "run-bbb", "fail")

    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "list", "--project", str(repo)])

    assert result.exit_code == 0
    # Both run uuids surfaced (or at least their distinguishing prefix).
    assert "run-aaa" in result.output
    assert "run-bbb" in result.output


def test_audit_list_json_array_of_runs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")
    _populate_audit_log(repo, "run-bbb", "fail")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "list", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    assert isinstance(data, list)
    uuids = {item["run_uuid"] for item in data}
    assert uuids == {"run-aaa", "run-bbb"}
    # Each item has at least final_verdict.
    for item in data:
        assert "final_verdict" in item


def test_audit_list_empty_log(tmp_path: Path) -> None:
    """No audit.jsonl yet → empty result, exit 0, no crash."""
    repo = _make_repo(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "list", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    assert data == []


# ---------------------------------------------------------------------------
# `ccbridge audit get`
# ---------------------------------------------------------------------------


def test_audit_get_specific_run_human(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")
    _populate_audit_log(repo, "run-bbb", "fail")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "get", "run-bbb", "--project", str(repo)]
    )

    assert result.exit_code == 0
    assert "run-bbb" in result.output
    assert "fail" in result.output.lower()
    # Other run not shown.
    assert "run-aaa" not in result.output


def test_audit_get_json_returns_run_details(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "get", "run-aaa", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    assert isinstance(data, dict)
    assert data["run_uuid"] == "run-aaa"
    assert data["final_verdict"] == "pass"
    assert isinstance(data.get("events"), list)
    assert len(data["events"]) >= 2  # started + verdict + complete


def test_audit_get_no_uuid_returns_last_run(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")
    _populate_audit_log(repo, "run-zzz", "needs_human")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "get", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    assert data["run_uuid"] == "run-zzz"


def test_audit_get_unknown_uuid_exit_nonzero(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["audit", "get", "nonexistent", "--project", str(repo)]
    )

    assert result.exit_code != 0
    assert "not found" in result.stderr.lower() or "nonexistent" in result.stderr.lower()


# ---------------------------------------------------------------------------
# `ccbridge audit watch`
# ---------------------------------------------------------------------------


def test_audit_watch_invokes_watcher_with_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: watch command wires up the watcher with a renderer.
    We don't run it long — stub watch_audit_log to just record args.
    """
    repo = _make_repo(tmp_path)
    captured: dict[str, Any] = {}

    def fake_watch(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(
        "ccbridge.cli.watch_audit_log", fake_watch
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "audit",
            "watch",
            "--project",
            str(repo),
            "--max-iterations",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert captured.get("audit_path") == repo / ".ccbridge" / "audit.jsonl"
    assert captured.get("max_iterations") == 1
    # Renderer must be a Renderer protocol satisfier.
    from ccbridge.renderers.base import Renderer

    assert isinstance(captured["renderer"], Renderer)


# ---------------------------------------------------------------------------
# `ccbridge status` (read-only minimal in 6a)
# ---------------------------------------------------------------------------


def test_status_no_runs_yet(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["status", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    assert data.get("project_dir") == str(repo.resolve())
    # No runs → empty / null fields.
    assert data.get("last_run") is None
    assert data.get("locked") is False


def test_status_with_runs(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "pass")
    _populate_audit_log(repo, "run-bbb", "fail")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["status", "--project", str(repo), "--json"]
    )

    assert result.exit_code == 0
    data = _assert_pure_json(result.stdout)
    last = data.get("last_run")
    assert last is not None
    assert last["run_uuid"] == "run-bbb"
    assert last["final_verdict"] == "fail"


def test_status_locked_when_lockfile_present(tmp_path: Path) -> None:
    """If a concurrent run holds the lockfile, status reports locked=true.
    """
    from ccbridge.core.lockfile import CCBridgeLock

    repo = _make_repo(tmp_path)
    held = CCBridgeLock(repo / ".ccbridge" / "lockfile", run_uuid="other-run")
    held.acquire()
    try:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["status", "--project", str(repo), "--json"]
        )
        assert result.exit_code == 0
        data = _assert_pure_json(result.stdout)
        assert data.get("locked") is True
        assert data.get("lock_run_uuid") == "other-run"
    finally:
        held.release()


# ---------------------------------------------------------------------------
# Cross-cutting: --json discipline
# ---------------------------------------------------------------------------


def test_json_mode_no_ansi_anywhere_in_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Across all commands in --json mode, stdout must contain no ANSI
    escapes — Claude/scripts could be parsing it.
    """
    repo = _make_repo(tmp_path)
    _populate_audit_log(repo, "run-aaa", "fail")
    _stub_codex(monkeypatch, _verdict_payload("pass"))

    runner = CliRunner()
    for argv in (
        ["audit", "list", "--project", str(repo), "--json"],
        ["audit", "get", "run-aaa", "--project", str(repo), "--json"],
        ["status", "--project", str(repo), "--json"],
    ):
        result = runner.invoke(cli, argv)
        assert result.exit_code == 0, f"failed for {argv}: {result.output}"
        assert "\x1b[" not in result.stdout, f"ANSI in stdout for {argv}"


# ---------------------------------------------------------------------------
# 6b commands deferred — skeleton must NOT crash, must say "not yet"
# ---------------------------------------------------------------------------


def test_stop_hook_subcommand_invokes_hook_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ccbridge stop-hook`` is the wrapper Claude Code invokes via
    .claude/settings.json. It just delegates to stop_hook_main and
    propagates its exit code.
    """
    called = {"flag": False, "return_value": 0}

    def fake_hook_main() -> int:
        called["flag"] = True
        return called["return_value"]

    monkeypatch.setattr("ccbridge.cli.stop_hook_main", fake_hook_main)

    runner = CliRunner()
    result = runner.invoke(cli, ["stop-hook"])

    assert called["flag"] is True
    assert result.exit_code == 0


def test_stop_hook_subcommand_propagates_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_hook_main() -> int:
        return 2

    monkeypatch.setattr("ccbridge.cli.stop_hook_main", fake_hook_main)

    runner = CliRunner()
    result = runner.invoke(cli, ["stop-hook"])
    assert result.exit_code == 2


def test_prompt_hook_subcommand_invokes_hook_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ccbridge prompt-hook`` is the wrapper Claude Code invokes via
    .claude/settings.json on UserPromptSubmit. It delegates to
    prompt_hook_main and propagates its exit code.
    """
    called = {"flag": False}

    def fake_prompt_main() -> int:
        called["flag"] = True
        return 0

    monkeypatch.setattr("ccbridge.cli.prompt_hook_main", fake_prompt_main)

    runner = CliRunner()
    result = runner.invoke(cli, ["prompt-hook"])

    assert called["flag"] is True
    assert result.exit_code == 0


def test_prompt_hook_subcommand_propagates_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_prompt_main() -> int:
        return 7

    monkeypatch.setattr("ccbridge.cli.prompt_hook_main", fake_prompt_main)

    runner = CliRunner()
    result = runner.invoke(cli, ["prompt-hook"])
    assert result.exit_code == 7


# NB: previously had stub-deferred-to-6b tests for init/uninstall;
# those commands are now implemented (see test_cli_init.py +
# test_cli_uninstall.py).
