"""Tests for ccbridge.core.event_bus."""

from __future__ import annotations

import logging

import pytest

from ccbridge.core.event_bus import EventBus
from ccbridge.core.events import StartedEvent, WarningEvent


@pytest.fixture
def sample_event() -> StartedEvent:
    return StartedEvent(
        run_uuid="run-1",
        project_name="P",
        project_id="id",
        iteration_count=0,
        max_iterations=3,
    )


def test_emit_with_no_listeners_is_noop(sample_event: StartedEvent) -> None:
    bus = EventBus()
    bus.emit(sample_event)
    assert bus.listener_count == 0


def test_emit_delivers_to_all_listeners(sample_event: StartedEvent) -> None:
    bus = EventBus()
    received_a: list[StartedEvent] = []
    received_b: list[StartedEvent] = []
    bus.subscribe(received_a.append)
    bus.subscribe(received_b.append)

    bus.emit(sample_event)

    assert received_a == [sample_event]
    assert received_b == [sample_event]


def test_listener_exception_does_not_break_bus(
    sample_event: StartedEvent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = EventBus()
    received: list[StartedEvent] = []

    def broken(event: StartedEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(received.append)  # registered after the broken one

    with caplog.at_level(logging.ERROR, logger="ccbridge.core.event_bus"):
        bus.emit(sample_event)

    assert received == [sample_event], "broken listener must not stop delivery"
    assert any("renderer/listener failed" in rec.message for rec in caplog.records)


def test_unsubscribe_removes_listener(sample_event: StartedEvent) -> None:
    bus = EventBus()
    received: list[StartedEvent] = []
    bus.subscribe(received.append)
    assert bus.unsubscribe(received.append) is True

    bus.emit(sample_event)
    assert received == []


def test_unsubscribe_unknown_listener_returns_false() -> None:
    bus = EventBus()
    assert bus.unsubscribe(lambda _: None) is False


def test_emit_supports_multiple_event_types() -> None:
    bus = EventBus()
    received: list[object] = []
    bus.subscribe(received.append)

    bus.emit(
        StartedEvent(
            run_uuid="r",
            project_name="P",
            project_id="id",
            iteration_count=0,
            max_iterations=3,
        )
    )
    bus.emit(WarningEvent(run_uuid="r", message="heads up"))

    assert len(received) == 2
    assert isinstance(received[0], StartedEvent)
    assert isinstance(received[1], WarningEvent)
