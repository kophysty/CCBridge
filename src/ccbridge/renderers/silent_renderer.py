"""SilentRenderer — collects events into a list.

Used for tests and as a reference implementation of the Renderer
Protocol (see :mod:`ccbridge.renderers.base`).
"""

from __future__ import annotations

from typing import TypeVar

from ccbridge.core.events import CCBridgeEvent

E = TypeVar("E", bound=CCBridgeEvent)


class SilentRenderer:
    """A no-output renderer that records every event for later inspection.

    Conforms to the :class:`Renderer` Protocol via ``__call__``.
    """

    def __init__(self) -> None:
        self.events: list[CCBridgeEvent] = []

    def __call__(self, event: CCBridgeEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()

    def events_of_type(self, cls: type[E]) -> list[E]:
        """Return all collected events that are instances of ``cls``."""
        return [e for e in self.events if isinstance(e, cls)]


__all__ = ("SilentRenderer",)
