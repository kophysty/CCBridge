"""Unit tests for the Renderer Protocol.

ADR-002 + ARCHITECTURE.md §2.9: renderers are broadcast-only listeners
on the EventBus. They render; they do NOT persist (orchestrator owns
audit.jsonl). The Protocol pins this minimal contract:

    Renderer(event: CCBridgeEvent) -> None

Anything callable that takes one CCBridgeEvent and returns None is a
Renderer. We use ``runtime_checkable`` so EventBus.subscribe can
optionally validate at registration time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ccbridge.core.events import CCBridgeEvent, StartedEvent
from ccbridge.renderers.base import Renderer


def test_plain_function_satisfies_renderer_protocol() -> None:
    def r(event: CCBridgeEvent) -> None:
        pass

    assert isinstance(r, Renderer)


def test_lambda_satisfies_renderer_protocol() -> None:
    r: Renderer = lambda event: None  # noqa: E731
    assert isinstance(r, Renderer)


def test_class_with_call_satisfies_renderer_protocol() -> None:
    class MyRenderer:
        def __call__(self, event: CCBridgeEvent) -> None:
            pass

    assert isinstance(MyRenderer(), Renderer)


def test_object_without_call_is_not_renderer() -> None:
    """Plain dict, string, etc. should NOT pass isinstance(Renderer)."""
    assert not isinstance({}, Renderer)
    assert not isinstance("not callable", Renderer)
    assert not isinstance(42, Renderer)


def test_renderer_can_be_invoked_with_event() -> None:
    """Sanity: a renderer-conformant callable accepts CCBridgeEvent
    instances. We invoke a stub to make sure the typing is honest.
    """
    received: list[CCBridgeEvent] = []

    def r(event: CCBridgeEvent) -> None:
        received.append(event)

    event = StartedEvent(
        run_uuid="r1",
        ts=datetime.now(UTC),
        project_name="proj",
        project_id="pid",
        iteration_count=1,
        max_iterations=3,
    )
    r(event)
    assert received == [event]
