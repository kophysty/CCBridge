"""In-process event bus for CCBridge.

Orchestrator emits events; renderers and audit log subscribe. Synchronous
fan-out keeps the v0.1 implementation simple — async or threaded
delivery can be added later without changing the API.

A failing listener never breaks the bus or other listeners. Errors are
logged via the standard `logging` module rather than re-emitted as
events (which would risk an infinite loop).

See ARCHITECTURE.md §2.9.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccbridge.core.events import CCBridgeEvent

logger = logging.getLogger(__name__)

Listener = Callable[["CCBridgeEvent"], None]


class EventBus:
    """Synchronous publish/subscribe bus.

    Not thread-safe by design — orchestrator runs single-threaded in v0.1.
    If concurrency is added later, wrap `_listeners` mutations with a lock
    or switch to a queue-based design.
    """

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        """Register a listener to receive every emitted event."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> bool:
        """Remove a previously-registered listener.

        Returns True if removed, False if not found.
        """
        try:
            self._listeners.remove(listener)
            return True
        except ValueError:
            return False

    def emit(self, event: CCBridgeEvent) -> None:
        """Deliver `event` to all listeners.

        Listener exceptions are caught and logged. They do not propagate
        to the caller and do not abort delivery to remaining listeners.
        """
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                logger.exception(
                    "renderer/listener failed for event_type=%s",
                    event.event_type,
                )

    @property
    def listener_count(self) -> int:
        return len(self._listeners)
