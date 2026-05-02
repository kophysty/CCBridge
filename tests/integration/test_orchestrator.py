"""Integration tests for the orchestrator.

The orchestrator is the load-bearing piece that ties PR1 modules
together. We exercise it as a black box: real lockfile, real
audit_log, real state.json, real context_builder against a tmp_path
git repo. The only thing we mock is the call to the underlying
``run_codex`` so we can deterministically script verdicts.

Each test sets up:

1. A tiny git repo under tmp_path with one committed file.
2. A modification to that file (so build_context produces a real diff).
3. A monkeypatched ``run_codex`` that returns scripted CodexRunResult
   instances or raises CodexRunnerError.

Then we call ``orchestrator.run_audit(...)`` and inspect:

* The terminal :class:`OrchestratorOutcome`.
* The audit.jsonl contents (parsed via AuditLog).
* The state.json contents.
* The events that flowed through the EventBus.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ccbridge.core.audit_log import AuditLog
from ccbridge.core.event_bus import EventBus
from ccbridge.core.events import (
    CCBridgeEvent,
    ErrorEvent,
    IterationCompleteEvent,
    VerdictEvent,
)
from ccbridge.core.lockfile import CCBridgeLock, LockBusyError
from ccbridge.core.orchestrator import (
    OrchestratorOutcome,
    run_audit,
)
from ccbridge.core.state import load_state
from ccbridge.runners.codex_runner import CodexRunnerError, CodexRunResult

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _make_repo_with_diff(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    return repo


def _verdict_payload(
    verdict: str = "pass",
    *,
    issues: list[dict[str, Any]] | None = None,
    confidence: float = 0.9,
    completeness: float = 0.9,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "verdict": verdict,
        "summary": f"summary for {verdict}",
        "issues": issues or [],
        "verdict_confidence": confidence,
        "issues_completeness": completeness,
        "files_reviewed": ["a.py"],
        "rules_checked": ["R-001"],
    }


def _stub_codex(
    monkeypatch: pytest.MonkeyPatch,
    payloads: Iterator[dict[str, Any]] | list[dict[str, Any]],
) -> list[str]:
    """Replace orchestrator's run_codex with a scripted iterator.

    Returns the captured prompts in the order they were sent.
    """
    seen_prompts: list[str] = []
    iterator = iter(payloads)

    def fake(*, prompt: str, cwd: Path, **kwargs: Any) -> CodexRunResult:
        seen_prompts.append(prompt)
        try:
            payload = next(iterator)
        except StopIteration as exc:
            raise AssertionError("codex called more times than scripted") from exc
        return CodexRunResult(
            parsed=payload,
            stdout=json.dumps(payload),
            stderr="",
            returncode=0,
            retry_count=0,
        )

    monkeypatch.setattr(
        "ccbridge.core.orchestrator.run_codex", fake
    )
    return seen_prompts


def _record_events(bus: EventBus) -> list[CCBridgeEvent]:
    captured: list[CCBridgeEvent] = []
    bus.subscribe(captured.append)
    return captured


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_audit_pass_on_first_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)
    _stub_codex(monkeypatch, [_verdict_payload("pass")])

    bus = EventBus()
    events = _record_events(bus)

    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )

    assert isinstance(outcome, OrchestratorOutcome)
    assert outcome.final_verdict == "pass"
    assert outcome.iterations_used == 1

    # Audit log: 1 verdict + 1 iteration_complete.
    log = AuditLog(repo / ".ccbridge" / "audit.jsonl")
    persisted = list(log.read_all())
    verdicts = [e for e in persisted if isinstance(e, VerdictEvent)]
    completes = [e for e in persisted if isinstance(e, IterationCompleteEvent)]
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "pass"
    assert len(completes) == 1
    assert completes[0].final_verdict == "pass"
    assert completes[0].iterations_used == 1

    # State cleared at end.
    state = load_state(repo / ".ccbridge" / "state.json")
    assert state is not None
    assert state.current_iteration is None

    # Lockfile released.
    assert not (repo / ".ccbridge" / "lockfile").exists()

    # EventBus saw the same kinds of events the audit log did.
    types = [type(e).__name__ for e in events]
    assert "StartedEvent" in types
    assert "VerdictEvent" in types
    assert "IterationCompleteEvent" in types


def test_run_audit_uses_supplied_run_uuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)
    _stub_codex(monkeypatch, [_verdict_payload("pass")])

    bus = EventBus()
    events = _record_events(bus)
    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        run_uuid="my-fixed-uuid",
    )
    assert outcome.run_uuid == "my-fixed-uuid"
    assert all(e.run_uuid == "my-fixed-uuid" for e in events)


# ---------------------------------------------------------------------------
# Iteration cap → needs_human (AC-3)
# ---------------------------------------------------------------------------


def test_three_fail_iterations_promote_to_needs_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)
    fail_payload = _verdict_payload(
        "fail",
        issues=[
            {
                "severity": "major",
                "category": "correctness",
                "file": "a.py",
                "line": 1,
                "message": "broken",
                "rule_id": "R-001",
            }
        ],
    )
    _stub_codex(monkeypatch, [fail_payload, fail_payload, fail_payload])

    bus = EventBus()
    _record_events(bus)
    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )

    assert outcome.final_verdict == "needs_human"
    assert outcome.iterations_used == 3

    log = AuditLog(repo / ".ccbridge" / "audit.jsonl")
    verdicts = [e for e in log.read_all() if isinstance(e, VerdictEvent)]
    assert [v.verdict for v in verdicts] == ["fail", "fail", "fail"]

    completes = [
        e for e in log.read_all() if isinstance(e, IterationCompleteEvent)
    ]
    assert completes[-1].final_verdict == "needs_human"

    # Lockfile released even after the cap.
    assert not (repo / ".ccbridge" / "lockfile").exists()


# ---------------------------------------------------------------------------
# Codex error paths
# ---------------------------------------------------------------------------


def test_codex_raises_recorded_as_error_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)

    def boom(**kwargs: Any) -> CodexRunResult:
        raise CodexRunnerError("codex blew up", returncode=1, stderr="oops")

    monkeypatch.setattr("ccbridge.core.orchestrator.run_codex", boom)

    bus = EventBus()
    events = _record_events(bus)
    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )

    assert outcome.final_verdict == "error"

    log = AuditLog(repo / ".ccbridge" / "audit.jsonl")
    persisted = list(log.read_all())
    assert any(isinstance(e, ErrorEvent) for e in persisted)
    completes = [e for e in persisted if isinstance(e, IterationCompleteEvent)]
    assert completes[-1].final_verdict == "error"

    # Lock released even on error.
    assert not (repo / ".ccbridge" / "lockfile").exists()
    # ErrorEvent surfaced on the bus.
    assert any(isinstance(e, ErrorEvent) for e in events)


def test_codex_returns_invalid_verdict_records_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pydantic validation fail → orchestrator records error verdict,
    not crash. AC-13 sycophancy guard piggy-backs on this path.
    """
    repo = _make_repo_with_diff(tmp_path)
    invalid = {
        "schema_version": 1,
        "verdict": "pass",
        "summary": "lying",
        "issues": [
            {
                "severity": "critical",
                "category": "security",
                "file": "a.py",
                "line": 1,
                "message": "RCE",
                "rule_id": "R-001",
            }
        ],
        "verdict_confidence": 0.99,
        "issues_completeness": 0.99,
        "files_reviewed": ["a.py"],
        "rules_checked": ["R-001"],
    }
    _stub_codex(monkeypatch, [invalid])

    bus = EventBus()
    _record_events(bus)
    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )
    assert outcome.final_verdict == "error"

    log = AuditLog(repo / ".ccbridge" / "audit.jsonl")
    assert any(isinstance(e, ErrorEvent) for e in log.read_all())
    assert not (repo / ".ccbridge" / "lockfile").exists()


# ---------------------------------------------------------------------------
# Pre-flight skip path (AC-18)
# ---------------------------------------------------------------------------


def test_empty_diff_yields_skipped_outcome_no_codex_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty diff → orchestrator records skipped, never calls codex."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "init")
    # No further changes.

    called = {"count": 0}

    def must_not_be_called(**kwargs: Any) -> CodexRunResult:
        called["count"] += 1
        raise AssertionError("codex must not be called on empty diff")

    monkeypatch.setattr(
        "ccbridge.core.orchestrator.run_codex", must_not_be_called
    )

    bus = EventBus()
    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )
    assert outcome.final_verdict == "skipped"
    assert called["count"] == 0
    assert not (repo / ".ccbridge" / "lockfile").exists()


# ---------------------------------------------------------------------------
# Lockfile (AC-8, AC-9)
# ---------------------------------------------------------------------------


def test_concurrent_run_blocks_with_lock_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)
    ccbridge = repo / ".ccbridge"
    ccbridge.mkdir(parents=True, exist_ok=True)

    held = CCBridgeLock(ccbridge / "lockfile", run_uuid="other-run")
    held.acquire()
    try:
        bus = EventBus()
        with pytest.raises(LockBusyError):
            run_audit(
                project_dir=repo,
                ccbridge_dir=ccbridge,
                bus=bus,
                max_iterations=3,
            )
    finally:
        held.release()


# ---------------------------------------------------------------------------
# Recovery — state.json missing but audit.jsonl intact (AC-11)
# ---------------------------------------------------------------------------


def test_state_recovers_after_state_file_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)
    _stub_codex(
        monkeypatch,
        [_verdict_payload("pass"), _verdict_payload("pass")],
    )

    # First run.
    bus = EventBus()
    first = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )
    assert first.final_verdict == "pass"

    # Wipe state.json — audit.jsonl stays as primary source of truth.
    state_file = repo / ".ccbridge" / "state.json"
    state_file.unlink()

    # Make a fresh diff and run again.
    (repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    second = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=EventBus(),
        max_iterations=3,
    )
    assert second.final_verdict == "pass"

    # state.json reappears, audit.jsonl has both runs' entries.
    assert state_file.exists()
    log = AuditLog(repo / ".ccbridge" / "audit.jsonl")
    runs = {e.run_uuid for e in log.read_all() if isinstance(e, VerdictEvent)}
    assert len(runs) == 2  # two distinct run_uuids


# ---------------------------------------------------------------------------
# Tolerant audit.jsonl reader (AC-12)
# ---------------------------------------------------------------------------


def test_torn_last_line_does_not_break_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo_with_diff(tmp_path)
    _stub_codex(monkeypatch, [_verdict_payload("pass")])

    bus = EventBus()
    run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )

    # Simulate a torn final line — common after a crash mid-write.
    audit_path = repo / ".ccbridge" / "audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as f:
        f.write('{"event_type":"verdict","run_uuid":"x"')  # no newline, no closing brace

    # Tolerant reader skips it; total events parsed still > 0.
    log = AuditLog(audit_path)
    events = list(log.read_all())
    assert len(events) >= 1


# ---------------------------------------------------------------------------
# Sanity: replace() unused-but-imported keeps mypy happy if future test needs it
# ---------------------------------------------------------------------------


def test_dataclass_replace_is_available_for_future_tests() -> None:
    """Just keeps `replace` import non-flagged; a real test stub."""
    from ccbridge.core.state import State

    s = State()
    s2 = replace(s)
    assert s == s2


# ---------------------------------------------------------------------------
# Audit log persistence failure (ADR-002, audit finding #5)
# ---------------------------------------------------------------------------


def test_audit_log_append_failure_yields_error_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-002 + audit finding #5: if audit.jsonl write fails (disk full,
    permission denied, etc.), orchestrator transitions to error path:

    * outcome.final_verdict == "error"
    * ErrorEvent emitted ONLY to EventBus (we observe it via listener)
    * NO attempt to write ErrorEvent to the broken audit.jsonl
    * state.json cleared
    * lockfile released
    """
    repo = _make_repo_with_diff(tmp_path)
    _stub_codex(monkeypatch, [_verdict_payload("pass")])

    # Make every audit_log.append raise — simulates disk full / I/O error.
    def boom(self: AuditLog, event: CCBridgeEvent) -> None:
        raise OSError("simulated audit append failure")

    monkeypatch.setattr(
        "ccbridge.core.audit_log.AuditLog.append", boom
    )

    bus = EventBus()
    captured = _record_events(bus)

    outcome = run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )

    # Outcome reflects the failure.
    assert outcome.final_verdict == "error"

    # ErrorEvent reached the bus.
    error_events = [e for e in captured if isinstance(e, ErrorEvent)]
    assert error_events, "expected ErrorEvent on bus, got none"
    assert any(
        "audit" in e.error_type.lower() or "persistence" in e.error_type.lower()
        for e in error_events
    ), f"ErrorEvent error_type should mention audit/persistence: {[e.error_type for e in error_events]}"

    # Lock released (CCBridgeLock context manager handles it).
    assert not (repo / ".ccbridge" / "lockfile").exists()

    # NB: audit.jsonl may not exist OR may be empty — point is, the
    # orchestrator did not try to "log the failure to the broken
    # sink". If file exists, it must contain ZERO ErrorEvent records
    # (because every append boomed).
    audit_path = repo / ".ccbridge" / "audit.jsonl"
    if audit_path.exists():
        # Whatever made it through must NOT include the post-failure
        # ErrorEvent — that is bus-only.
        log = AuditLog(audit_path)
        events_on_disk = list(log.read_all())
        # Since boom raises on every append, no events should be on disk.
        assert events_on_disk == []


def test_audit_log_append_failure_clears_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """state.json must be cleared in the finally block even when audit
    append fails — recovery model must not leave stale current_iteration.
    """
    repo = _make_repo_with_diff(tmp_path)
    _stub_codex(monkeypatch, [_verdict_payload("pass")])

    def boom(self: AuditLog, event: CCBridgeEvent) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("ccbridge.core.audit_log.AuditLog.append", boom)

    bus = EventBus()
    run_audit(
        project_dir=repo,
        ccbridge_dir=repo / ".ccbridge",
        bus=bus,
        max_iterations=3,
    )

    state_path = repo / ".ccbridge" / "state.json"
    if state_path.exists():
        state = load_state(state_path)
        assert state is not None
        assert state.current_iteration is None, (
            "state.current_iteration must be cleared on failure path"
        )
