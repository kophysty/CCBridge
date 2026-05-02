"""CCBridge events for the EventBus.

Renderers (rich, silent, future Wave/MCP) consume the same stream of
typed events. This module defines the event vocabulary; orchestrator
emits, renderers subscribe. Persistence in ``audit.jsonl`` is handled
by the orchestrator itself (see ADR-002), not by a renderer.

See ARCHITECTURE.md §2.9 for the design rationale and ADR-002 for the
ownership decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VerdictLabel = Literal["pass", "fail", "needs_human", "error", "skipped"]
ContextLevel = Literal["minimal", "medium", "full"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CCBridgeEvent(BaseModel):
    """Base class for all CCBridge events flowing through the EventBus.

    Every event is JSON-serializable so it can be written to audit.jsonl
    or shipped over MCP without further transformation.
    """

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        ser_json_timedelta="iso8601",
    )

    event_type: str
    ts: datetime = Field(default_factory=_utc_now)
    run_uuid: str
    iteration_id: str | None = None


class StartedEvent(CCBridgeEvent):
    event_type: Literal["started"] = "started"
    project_name: str
    project_id: str
    iteration_count: int
    max_iterations: int


class ContextBuiltEvent(CCBridgeEvent):
    event_type: Literal["context_built"] = "context_built"
    diff_lines: int
    files_count: int
    rules_count: int
    context_level: ContextLevel
    cache_hit: bool
    estimated_tokens: int | None = None


class CodexThinkingEvent(CCBridgeEvent):
    """Emitted right before Codex CLI is invoked.

    `eta_seconds` is a hint for renderers (rough estimate from history).
    """

    event_type: Literal["codex_thinking"] = "codex_thinking"
    eta_seconds: int | None = None


class IssueSummary(BaseModel):
    """Aggregated issues count by severity, for events.

    The full Issue list lives in the verdict and audit log, not in events
    (events are meant to be small and broadcast cheaply).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    critical: int = 0
    major: int = 0
    minor: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.major + self.minor + self.info


class VerdictEvent(CCBridgeEvent):
    event_type: Literal["verdict"] = "verdict"
    verdict: VerdictLabel
    summary: str
    issues: IssueSummary
    cost_usd: float
    duration_sec: float
    verdict_confidence: float
    issues_completeness: float


class IterationCompleteEvent(CCBridgeEvent):
    """Emitted at the end of a full audit run (terminal state)."""

    event_type: Literal["iteration_complete"] = "iteration_complete"
    final_verdict: VerdictLabel
    iterations_used: int
    total_cost_usd: float
    duration_sec: float


class ErrorEvent(CCBridgeEvent):
    """Operational error (timeout, lock failure, invalid JSON from Codex, ...).

    Distinct from a verdict of `error` (which is a successful review with
    a structured failure outcome). ErrorEvent indicates the pipeline itself
    misbehaved.
    """

    event_type: Literal["error"] = "error"
    error_type: str
    message: str
    will_retry: bool = False
    retry_count: int = 0


class WarningEvent(CCBridgeEvent):
    """Non-fatal warning. Examples: dropped issue (semantic validation),
    stale lock recovered, schema migration applied, BOM stripped.
    """

    event_type: Literal["warning"] = "warning"
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Discriminated union for parsing arbitrary events from JSON (audit.jsonl, MCP).
# ---------------------------------------------------------------------------


AnyEvent = (
    StartedEvent
    | ContextBuiltEvent
    | CodexThinkingEvent
    | VerdictEvent
    | IterationCompleteEvent
    | ErrorEvent
    | WarningEvent
)


_EVENT_REGISTRY: dict[str, type[CCBridgeEvent]] = {
    "started": StartedEvent,
    "context_built": ContextBuiltEvent,
    "codex_thinking": CodexThinkingEvent,
    "verdict": VerdictEvent,
    "iteration_complete": IterationCompleteEvent,
    "error": ErrorEvent,
    "warning": WarningEvent,
}


def parse_event(data: dict[str, Any]) -> CCBridgeEvent:
    """Reconstruct a typed event from a dict (e.g. an audit.jsonl line).

    Raises ValueError if event_type is unknown or data fails validation.
    """
    event_type = data.get("event_type")
    if event_type is None:
        raise ValueError("missing event_type")
    cls = _EVENT_REGISTRY.get(event_type)
    if cls is None:
        raise ValueError(f"unknown event_type: {event_type}")
    return cls.model_validate(data)
