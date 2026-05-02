"""Event renderers for CCBridge.

Per ADR-002, renderers are broadcast-only listeners on the EventBus.
They display, log, or notify based on incoming CCBridgeEvent stream;
they do NOT write to ``audit.jsonl`` (the orchestrator owns that).

Available renderers (PR2b):

* :class:`SilentRenderer` — collects events into a list (tests).
* :class:`RichRenderer` — formats events for terminal stdout (Stop hook).

Future (deferred):

* ``WaveRenderer`` (v0.2) — wsh badge / tab notifications in Wave.
* ``MCPRenderer`` (v0.3) — events as MCP tool_result.
"""

from ccbridge.renderers.base import Renderer

__all__ = ("Renderer",)
