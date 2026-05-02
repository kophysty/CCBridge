"""Tests for ccbridge.core.events."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ccbridge.core.events import (
    CodexThinkingEvent,
    ContextBuiltEvent,
    ErrorEvent,
    IssueSummary,
    IterationCompleteEvent,
    StartedEvent,
    VerdictEvent,
    WarningEvent,
    parse_event,
)


def test_started_event_minimal_fields() -> None:
    event = StartedEvent(
        run_uuid="run-1",
        project_name="Varya",
        project_id="proj-1",
        iteration_count=0,
        max_iterations=3,
    )
    assert event.event_type == "started"
    assert event.iteration_id is None
    assert isinstance(event.ts, datetime)
    assert event.ts.tzinfo == UTC


def test_event_is_frozen() -> None:
    event = StartedEvent(
        run_uuid="run-1",
        project_name="Varya",
        project_id="proj-1",
        iteration_count=0,
        max_iterations=3,
    )
    with pytest.raises(Exception):  # noqa: B017 — pydantic emits ValidationError on frozen
        event.run_uuid = "tampered"  # type: ignore[misc]


def test_context_built_event_serialization_roundtrip() -> None:
    event = ContextBuiltEvent(
        run_uuid="run-2",
        iteration_id="iter-1",
        diff_lines=120,
        files_count=4,
        rules_count=12,
        context_level="medium",
        cache_hit=True,
        estimated_tokens=8000,
    )
    payload = event.model_dump(mode="json")
    raw = json.dumps(payload)
    restored = parse_event(json.loads(raw))
    assert isinstance(restored, ContextBuiltEvent)
    assert restored.diff_lines == 120
    assert restored.cache_hit is True


def test_codex_thinking_event_optional_eta() -> None:
    event = CodexThinkingEvent(run_uuid="run-3", iteration_id="iter-1")
    assert event.eta_seconds is None


def test_issue_summary_total_property() -> None:
    summary = IssueSummary(critical=1, major=2, minor=3, info=4)
    assert summary.total == 10


def test_verdict_event_carries_aggregated_issues_only() -> None:
    summary = IssueSummary(major=1, minor=1)
    event = VerdictEvent(
        run_uuid="run-4",
        iteration_id="iter-1",
        verdict="fail",
        summary="2 issues found",
        issues=summary,
        cost_usd=0.12,
        duration_sec=42.0,
        verdict_confidence=0.85,
        issues_completeness=0.92,
    )
    assert event.issues.total == 2
    assert event.verdict == "fail"


def test_iteration_complete_event() -> None:
    event = IterationCompleteEvent(
        run_uuid="run-5",
        final_verdict="pass",
        iterations_used=2,
        total_cost_usd=0.20,
        duration_sec=85.0,
    )
    assert event.final_verdict == "pass"


def test_error_event_defaults() -> None:
    event = ErrorEvent(
        run_uuid="run-6",
        error_type="codex_timeout",
        message="Codex did not respond in 300s",
    )
    assert event.will_retry is False
    assert event.retry_count == 0


def test_warning_event_with_context() -> None:
    event = WarningEvent(
        run_uuid="run-7",
        message="Issue dropped: file not in diff",
        context={"file": "src/foo.py", "line": 42},
    )
    assert event.context["file"] == "src/foo.py"


def test_parse_event_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown event_type"):
        parse_event({"event_type": "made_up", "run_uuid": "x"})


def test_parse_event_missing_type_raises() -> None:
    with pytest.raises(ValueError, match="missing event_type"):
        parse_event({"run_uuid": "x"})


def test_parse_event_extra_fields_ignored() -> None:
    """Forward compatibility: events with newer fields don't break the reader."""
    raw = {
        "event_type": "started",
        "run_uuid": "r",
        "project_name": "P",
        "project_id": "id",
        "iteration_count": 0,
        "max_iterations": 3,
        "ts": "2026-05-02T10:00:00+00:00",
        "future_field": "ignored",
    }
    event = parse_event(raw)
    assert isinstance(event, StartedEvent)
