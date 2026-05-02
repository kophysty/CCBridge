"""Event renderers for CCBridge.

Per ADR-002, renderers are broadcast-only listeners on the EventBus.
They display, log, or notify based on incoming CCBridgeEvent stream;
they do NOT write to ``audit.jsonl`` (the orchestrator owns that).

Available renderers (PR2b):

* :class:`SilentRenderer` — collects events into a list (tests).
* :class:`RichRenderer` — formats events for terminal output. Caller
  decides destination via the ``file=`` constructor argument:

  - In ``transports/stop_hook`` → bound to **stderr** (stdout is
    reserved for the decision JSON Claude parses).
  - In ``cli ccbridge audit run`` → bound to **stdout** (terminal
    user, no parser).
  - In ``transports/audit_watch`` → bound to **stdout** (separate
    process, watches audit.jsonl directly).

Future (deferred):

* ``WaveRenderer`` (v0.2) — wsh badge / tab notifications in Wave.
* ``MCPRenderer`` (v0.3) — events as MCP tool_result.
"""

from ccbridge.renderers.base import Renderer

__all__ = ("Renderer",)
