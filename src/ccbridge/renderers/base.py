"""Renderer Protocol — minimal contract for EventBus listeners.

Per ADR-002, renderers are broadcast-only listeners. They consume
``CCBridgeEvent`` instances from the EventBus and display, log, or
notify accordingly. They MUST NOT write to ``audit.jsonl`` — that is
the orchestrator's responsibility.

The Protocol intentionally keeps the surface as small as possible:
any callable that takes one event and returns None qualifies. This
matches how :func:`EventBus.subscribe` already works (accepts any
``Callable[[CCBridgeEvent], None]``) and avoids forcing renderer
authors to subclass.

``runtime_checkable`` makes :func:`isinstance` checks work, which
helps tests pin the contract without needing static type checks.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ccbridge.core.events import CCBridgeEvent


@runtime_checkable
class Renderer(Protocol):
    """A callable that handles one event."""

    def __call__(self, event: CCBridgeEvent) -> None: ...


__all__ = ("Renderer",)
