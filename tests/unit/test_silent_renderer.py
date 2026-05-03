"""Unit tests for SilentRenderer.

The silent renderer just collects events into a list. It exists for
tests (and as a reference implementation of the Renderer Protocol).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ccbridge.core.events import (
    ErrorEvent,
    IssueSummary,
    StartedEvent,
    VerdictEvent,
)
from ccbridge.renderers.base import Renderer
from ccbridge.renderers.silent_renderer import SilentRenderer


def _started(uuid: str = "r1") -> StartedEvent:
    return StartedEvent(
        run_uuid=uuid,
        ts=datetime.now(UTC),
        project_name="proj",
        project_id="pid",
        iteration_count=1,
        max_iterations=3,
    )


def _verdict(uuid: str = "r1") -> VerdictEvent:
    return VerdictEvent(
        run_uuid=uuid,
        ts=datetime.now(UTC),
        verdict="pass",
        summary="ok",
        issues=IssueSummary(),
        cost_usd=0.0,
        duration_sec=1.0,
        verdict_confidence=0.9,
        issues_completeness=0.9,
    )


def test_silent_renderer_satisfies_protocol() -> None:
    assert isinstance(SilentRenderer(), Renderer)


def test_silent_renderer_collects_events_in_order() -> None:
    r = SilentRenderer()
    e1 = _started()
    e2 = _verdict()

    r(e1)
    r(e2)

    assert r.events == [e1, e2]


def test_silent_renderer_starts_empty() -> None:
    assert SilentRenderer().events == []


def test_silent_renderer_clear_resets_history() -> None:
    r = SilentRenderer()
    r(_started())
    r(_verdict())
    r.clear()
    assert r.events == []


def test_silent_renderer_filter_by_type() -> None:
    """Convenience: filter events by class for assertion ergonomics."""
    r = SilentRenderer()
    r(_started())
    r(_verdict())
    r(
        ErrorEvent(
            run_uuid="r1",
            ts=datetime.now(UTC),
            error_type="x",
            message="boom",
        )
    )

    verdicts = r.events_of_type(VerdictEvent)
    assert len(verdicts) == 1
    assert verdicts[0].verdict == "pass"

    errors = r.events_of_type(ErrorEvent)
    assert len(errors) == 1
    assert errors[0].error_type == "x"


def test_silent_renderer_does_not_swallow_listener_in_bus() -> None:
    """SilentRenderer is plain — it does not do error suppression of
    its own. Errors raised inside (if user passes a buggy subclass)
    would propagate; the EventBus catches listener errors at its level.
    """
    r = SilentRenderer()
    r(_started())
    assert len(r.events) == 1
