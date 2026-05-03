"""Integration tests for transports/stop_hook.

Stop hook is invoked by Claude Code with JSON on stdin:

    {"stop_hook_active": false, "cwd": "...", ...}

CLAUDE_PROJECT_DIR is set in env. The hook decides whether to block
Claude from stopping (verdict=fail), let Claude stop with a non-block
signal (needs_human/error/lockbusy), or do nothing (pass / no-op).

Contract — stdout reserved strictly for decision JSON:

* pass / stop_hook_active=true / fail-open path → exit 0, empty stdout
* fail → exit 0 + {"decision":"block","reason":"<plain text>"}
* needs_human / error / skipped / lock_busy → exit 0 +
  {"continue":false,"stopReason":"<plain text>"}

Implementation must:

* Honour CLAUDE_PROJECT_DIR (env), fall back to cwd in input.
* Validate project dir exists (Path.resolve).
* Recursion guard: stop_hook_active=true → no-op.
* fail open: any internal exception → empty stdout, error to stderr,
  exit 0.
* stdout strictly JSON or empty — NEVER ANSI / rich output.
* Never log env vars / secrets.

Source: https://code.claude.com/docs/en/hooks (Stop event semantics).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ccbridge.core.orchestrator import OrchestratorOutcome
from ccbridge.runners.codex_runner import CodexRunResult
from ccbridge.transports.stop_hook import stop_hook_main

# ---------------------------------------------------------------------------
# Helpers — git repo for live runs (when we want orchestrator to actually
# do work). For most tests we just stub run_audit directly.
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
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    return repo


def _stub_outcome(
    monkeypatch: pytest.MonkeyPatch, outcome: OrchestratorOutcome
) -> None:
    """Replace run_audit with a stub that returns a scripted outcome."""

    def fake_run_audit(**kwargs: Any) -> OrchestratorOutcome:
        return outcome

    monkeypatch.setattr(
        "ccbridge.transports.stop_hook.run_audit_with_config", fake_run_audit
    )


def _make_outcome(
    verdict: str = "pass", *, summary: str = "ok"
) -> OrchestratorOutcome:
    return OrchestratorOutcome(
        run_uuid="r1",
        final_verdict=verdict,
        iterations_used=1,
        duration_sec=1.0,
    )


def _stub_codex_payload(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """For live-orchestrator tests: stub codex inside orchestrator."""

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


@dataclass(frozen=True)
class HookResult:
    """Captured output of a single stop_hook_main() invocation."""

    exit_code: int
    stdout: str
    stderr: str


def _run_hook(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    stdin: dict[str, Any],
    project_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> HookResult:
    """Invoke stop_hook_main with given stdin and env."""
    import sys
    from io import StringIO

    if env is not None:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    if project_dir is not None:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(stdin)))

    exit_code = stop_hook_main()
    captured = capsys.readouterr()
    return HookResult(exit_code=exit_code, stdout=captured.out, stderr=captured.err)


def _assert_stdout_is_empty_or_valid_json(stdout: str) -> dict[str, Any] | None:
    """The hard contract: stdout is either empty or a single JSON object.
    Never ANSI, never plain text, never anything else.
    """
    text = stdout.strip()
    if not text:
        return None
    parsed = json.loads(text)  # will raise if not JSON
    assert isinstance(parsed, dict)
    return parsed


# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------


def test_stop_hook_active_true_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per Claude Code docs, stop_hook_active=true means we are already
    inside a hook-triggered Claude session. We must not start another
    audit, otherwise we recurse forever.
    """
    called = {"flag": False}

    def must_not_run(**kwargs: Any) -> OrchestratorOutcome:
        called["flag"] = True
        raise AssertionError("run_audit must not be called when stop_hook_active=true")

    monkeypatch.setattr(
        "ccbridge.transports.stop_hook.run_audit_with_config", must_not_run
    )

    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": True, "cwd": str(repo)},
        project_dir=repo,
    )

    assert result.exit_code == 0
    assert called["flag"] is False
    assert _assert_stdout_is_empty_or_valid_json(result.stdout) is None
    # stderr может содержать диагностику но не обязан.


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


def test_project_root_from_claude_project_dir_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _make_repo(tmp_path)
    captured_kwargs: dict[str, Any] = {}

    def capture(**kwargs: Any) -> OrchestratorOutcome:
        captured_kwargs.update(kwargs)
        return _make_outcome("pass")

    monkeypatch.setattr("ccbridge.transports.stop_hook.run_audit_with_config", capture)

    result = _run_hook(
        monkeypatch,
        capsys,
        # cwd in input is a different (irrelevant) path; env wins.
        stdin={"stop_hook_active": False, "cwd": str(tmp_path / "elsewhere")},
        project_dir=repo,
    )

    assert result.exit_code == 0
    assert captured_kwargs.get("project_dir") == repo.resolve()


def test_project_root_falls_back_to_cwd_when_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    captured_kwargs: dict[str, Any] = {}

    def capture(**kwargs: Any) -> OrchestratorOutcome:
        captured_kwargs.update(kwargs)
        return _make_outcome("pass")

    monkeypatch.setattr("ccbridge.transports.stop_hook.run_audit_with_config", capture)

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
    )

    assert result.exit_code == 0
    assert captured_kwargs.get("project_dir") == repo.resolve()


def test_project_root_invalid_returns_fail_open_zero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both env and input cwd are missing/broken — we must NOT block
    Claude. Empty stdout (no JSON), exit 0, error to stderr.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    def must_not_run(**kwargs: Any) -> OrchestratorOutcome:
        raise AssertionError("run_audit must not be called")

    monkeypatch.setattr(
        "ccbridge.transports.stop_hook.run_audit_with_config", must_not_run
    )

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": "/nonexistent/path/xyz"},
    )

    assert result.exit_code == 0
    assert _assert_stdout_is_empty_or_valid_json(result.stdout) is None
    assert result.stderr  # diagnostic written to stderr


# ---------------------------------------------------------------------------
# Verdict outcomes
# ---------------------------------------------------------------------------


def test_pass_outcome_yields_empty_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _make_repo(tmp_path)
    _stub_outcome(monkeypatch, _make_outcome("pass"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert _assert_stdout_is_empty_or_valid_json(result.stdout) is None


def test_fail_outcome_emits_decision_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _make_repo(tmp_path)
    _stub_outcome(monkeypatch, _make_outcome("fail"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    parsed = _assert_stdout_is_empty_or_valid_json(result.stdout)
    assert parsed is not None
    assert parsed.get("decision") == "block"
    assert isinstance(parsed.get("reason"), str)
    # No ANSI escapes in reason — must be plain text.
    assert "\x1b[" not in parsed["reason"]


def test_needs_human_outcome_emits_continue_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """needs_human → not decision:block (which would mean "keep going").
    We use {"continue": false, "stopReason": ...} so Claude actually
    stops AND the user sees why.
    """
    repo = _make_repo(tmp_path)
    _stub_outcome(monkeypatch, _make_outcome("needs_human"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    parsed = _assert_stdout_is_empty_or_valid_json(result.stdout)
    assert parsed is not None
    assert parsed.get("continue") is False
    assert "decision" not in parsed
    assert isinstance(parsed.get("stopReason"), str)
    assert "needs_human" in parsed["stopReason"].lower() or "human" in parsed["stopReason"].lower()


def test_error_outcome_emits_continue_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _make_repo(tmp_path)
    _stub_outcome(monkeypatch, _make_outcome("error"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    parsed = _assert_stdout_is_empty_or_valid_json(result.stdout)
    assert parsed is not None
    assert parsed.get("continue") is False
    assert "decision" not in parsed


def test_skipped_outcome_emits_continue_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """skipped (empty/binary diff) — we don't block Claude, but we tell
    the user via stopReason that no review happened.
    """
    repo = _make_repo(tmp_path)
    _stub_outcome(monkeypatch, _make_outcome("skipped"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    parsed = _assert_stdout_is_empty_or_valid_json(result.stdout)
    assert parsed is not None
    assert parsed.get("continue") is False


# ---------------------------------------------------------------------------
# LockBusy → not decision:block, just continue:false
# ---------------------------------------------------------------------------


def test_lock_busy_emits_continue_false_not_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Concurrent audit run already in progress. We must NOT
    decision:block (that would tell Claude "keep going" — but
    there's no reviewer to satisfy because someone else is using it).
    Instead: continue:false with informational message.
    """
    from datetime import UTC, datetime

    from ccbridge.core.lockfile import LockBusyError, LockHolder

    repo = _make_repo(tmp_path)
    holder = LockHolder(
        pid=99999,
        hostname="test",
        started_at=datetime.now(UTC),
        run_uuid="other-run",
    )

    def lock_busy(**kwargs: Any) -> OrchestratorOutcome:
        raise LockBusyError(holder)

    monkeypatch.setattr("ccbridge.transports.stop_hook.run_audit_with_config", lock_busy)

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    parsed = _assert_stdout_is_empty_or_valid_json(result.stdout)
    assert parsed is not None
    assert parsed.get("continue") is False
    assert "decision" not in parsed
    assert "running" in parsed.get("stopReason", "").lower() or "concurrent" in parsed.get("stopReason", "").lower()


# ---------------------------------------------------------------------------
# Fail-open: malformed input must not crash Claude session
# ---------------------------------------------------------------------------


def test_malformed_stdin_json_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Garbage on stdin must NOT crash the hook. Empty stdout, exit 0,
    stderr explains the issue. Claude continues normally.
    """
    import sys
    from io import StringIO

    monkeypatch.setattr(sys, "stdin", StringIO("not valid json {{{"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    def must_not_run(**kwargs: Any) -> OrchestratorOutcome:
        raise AssertionError("run_audit must not be called on malformed input")

    monkeypatch.setattr(
        "ccbridge.transports.stop_hook.run_audit_with_config", must_not_run
    )

    exit_code = stop_hook_main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert _assert_stdout_is_empty_or_valid_json(captured.out) is None
    assert captured.err  # diagnostic written


def test_orchestrator_unexpected_exception_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unhandled exception in run_audit must NOT crash Claude.
    Empty stdout, exit 0, stderr describes the issue.
    """
    repo = _make_repo(tmp_path)

    def boom(**kwargs: Any) -> OrchestratorOutcome:
        raise RuntimeError("unexpected internal error")

    monkeypatch.setattr("ccbridge.transports.stop_hook.run_audit_with_config", boom)

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert _assert_stdout_is_empty_or_valid_json(result.stdout) is None
    assert (
        "unexpected internal error" in result.stderr
        or "error" in result.stderr.lower()
    )


# ---------------------------------------------------------------------------
# Stdout discipline — never ANSI, never rich output
# ---------------------------------------------------------------------------


def test_stdout_never_contains_ansi_escapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The reason field is plain text. No ANSI escapes from rich
    formatting must leak into stdout — Claude reads stdout as a JSON
    string, and ANSI inside JSON would corrupt the parse.
    """
    repo = _make_repo(tmp_path)
    _stub_outcome(monkeypatch, _make_outcome("fail"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert "\x1b[" not in result.stdout  # no ANSI CSI sequences
    assert "\x1b]" not in result.stdout  # no OSC


def test_secrets_not_logged_to_stdout_or_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If env contains API keys, stop_hook must not echo them anywhere
    (not into reason, not into stderr diagnostics).
    """
    repo = _make_repo(tmp_path)
    secret = "sk-DO-NOT-LEAK-THIS-MARKER-Z42"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    _stub_outcome(monkeypatch, _make_outcome("fail"))

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={"stop_hook_active": False, "cwd": str(repo)},
        project_dir=repo,
    )
    assert secret not in result.stdout
    assert secret not in result.stderr
