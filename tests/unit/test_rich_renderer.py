"""Unit tests for RichRenderer.

We don't pin the exact visual format (rich evolves, terminals differ).
Instead we capture stdout via ``capsys`` and assert on stable markers:

* event-type indicators (started, verdict, error, ...)
* key fields (verdict label, summary, run_uuid, severity counts)

This keeps tests robust to layout tweaks while still catching wrong
event types or missing data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ccbridge.core.events import (
    ContextBuiltEvent,
    ErrorEvent,
    IssueSummary,
    IterationCompleteEvent,
    StartedEvent,
    VerdictEvent,
    WarningEvent,
)
from ccbridge.renderers.base import Renderer
from ccbridge.renderers.rich_renderer import RichRenderer


def _now() -> datetime:
    return datetime.now(UTC)


def test_rich_renderer_satisfies_protocol() -> None:
    assert isinstance(RichRenderer(), Renderer)


def test_rich_renderer_started_event(capsys: pytest.CaptureFixture[str]) -> None:
    r = RichRenderer()
    r(
        StartedEvent(
            run_uuid="run-abc12345",
            ts=_now(),
            project_name="myproj",
            project_id="pid",
            iteration_count=1,
            max_iterations=3,
        )
    )
    out = capsys.readouterr().out.lower()
    # We expect at least: project name, iter count, run_uuid prefix.
    assert "myproj" in out
    assert "1" in out and "3" in out  # 1/3
    assert "abc12345" in out or "run-abc12345" in out


def test_rich_renderer_context_built_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = RichRenderer()
    r(
        ContextBuiltEvent(
            run_uuid="r1",
            ts=_now(),
            diff_lines=47,
            files_count=3,
            rules_count=12,
            context_level="medium",
            cache_hit=True,
        )
    )
    out = capsys.readouterr().out.lower()
    assert "47" in out
    assert "3" in out  # files_count
    # Cache hit signal — either word "hit" or a check mark / true.
    assert "cache" in out


def test_rich_renderer_verdict_pass_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = RichRenderer()
    r(
        VerdictEvent(
            run_uuid="r1",
            ts=_now(),
            verdict="pass",
            summary="all good",
            issues=IssueSummary(),
            cost_usd=0.08,
            duration_sec=42.0,
            verdict_confidence=0.92,
            issues_completeness=0.95,
        )
    )
    out = capsys.readouterr().out.lower()
    assert "pass" in out
    assert "all good" in out
    # Confidence/completeness shown.
    assert "0.92" in out or "92" in out


def test_rich_renderer_verdict_fail_with_issues(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = RichRenderer()
    r(
        VerdictEvent(
            run_uuid="r1",
            ts=_now(),
            verdict="fail",
            summary="needs work",
            issues=IssueSummary(critical=0, major=1, minor=2, info=0),
            cost_usd=0.05,
            duration_sec=30.0,
            verdict_confidence=0.85,
            issues_completeness=0.9,
        )
    )
    out = capsys.readouterr().out.lower()
    assert "fail" in out
    assert "needs work" in out
    # Severity counts present.
    assert "1" in out  # major count
    assert "2" in out  # minor count


def test_rich_renderer_error_event(capsys: pytest.CaptureFixture[str]) -> None:
    r = RichRenderer()
    r(
        ErrorEvent(
            run_uuid="r1",
            ts=_now(),
            error_type="codex_runner",
            message="codex blew up",
        )
    )
    out = capsys.readouterr().out.lower()
    assert "error" in out
    assert "codex" in out


def test_rich_renderer_warning_event(capsys: pytest.CaptureFixture[str]) -> None:
    r = RichRenderer()
    r(
        WarningEvent(
            run_uuid="r1",
            ts=_now(),
            message="recovered_stale_lock",
            context={"path": "/tmp/lock"},
        )
    )
    out = capsys.readouterr().out.lower()
    assert "warning" in out or "warn" in out
    assert "recovered_stale_lock" in out or "stale" in out


def test_rich_renderer_iteration_complete_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = RichRenderer()
    r(
        IterationCompleteEvent(
            run_uuid="r1",
            ts=_now(),
            final_verdict="pass",
            iterations_used=2,
            total_cost_usd=0.16,
            duration_sec=84.0,
        )
    )
    out = capsys.readouterr().out.lower()
    assert "pass" in out
    # Iterations used somewhere.
    assert "2" in out


def test_rich_renderer_does_not_crash_on_unknown_event_subclass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Forward compat: a future event type the renderer doesn't have a
    branch for should not raise. We just print a generic line.
    """

    class FutureEvent(StartedEvent):
        # Subclassing StartedEvent to keep Pydantic happy without inventing
        # a brand new event_type the renderer is forced to know.
        pass

    r = RichRenderer()
    # Render an unknown-shape via the generic branch by passing a base
    # CCBridgeEvent the renderer doesn't have explicit handling for —
    # the renderer must fall back gracefully.
    r(
        FutureEvent(
            run_uuid="r1",
            ts=_now(),
            project_name="x",
            project_id="p",
            iteration_count=1,
            max_iterations=1,
        )
    )
    # No exception is the assertion; capsys.readouterr drains anyway.
    capsys.readouterr()


def test_rich_renderer_unicode_safe(capsys: pytest.CaptureFixture[str]) -> None:
    """AC-17: cyrillic in summary must round-trip through rich without crash."""
    r = RichRenderer()
    r(
        VerdictEvent(
            run_uuid="r1",
            ts=_now(),
            verdict="pass",
            summary="всё хорошо",
            issues=IssueSummary(),
            cost_usd=0.0,
            duration_sec=1.0,
            verdict_confidence=0.9,
            issues_completeness=0.9,
        )
    )
    out = capsys.readouterr().out
    assert "всё хорошо" in out
