"""The orchestrator — main review loop that ties PR1 modules together.

Lifecycle of a single ``run_audit`` call::

    acquire lockfile
        for iteration in 1..max_iterations:
            build context (diff snapshot + prompt)
                empty/binary diff → record skipped, break
                too large       → record error, break
            emit StartedEvent (first iter only)
            emit ContextBuiltEvent
            emit CodexThinkingEvent
            call run_codex
                on error → record ErrorEvent, break
            parse Verdict (Pydantic)
                on validation failure → record ErrorEvent, break
            run validate_semantics → ValidatedVerdict
            emit VerdictEvent + append audit.jsonl
            update state.json
            if effective verdict ∈ {pass, needs_human, skipped, error}: break
        emit IterationCompleteEvent + append audit.jsonl
        clear current_iteration in state
    release lockfile (always, even on error)

Order of writes matters (ARCHITECTURE.md §2.4): we append to
``audit.jsonl`` BEFORE writing ``state.json``, so a crash between the
two does not lose the verdict that already cost real Codex tokens.

The orchestrator is fully synchronous and single-threaded. Cross-
process safety comes from :class:`CCBridgeLock` (portalocker O_EXCL
semantics), not from threading primitives.

Recovery model (ARCHITECTURE.md §2.4):

* If ``state.json`` is missing or stale, callers can rebuild from the
  last entries of ``audit.jsonl`` — that's outside the scope of
  ``run_audit``, which always starts a fresh ``run_uuid``. Inspecting
  history is the job of ``cli.audit list/get/status``.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ccbridge.core.audit_log import AuditLog
from ccbridge.core.context_builder import (
    BuiltContext,
    ContextSkipped,
    ContextTooLargeError,
    build_context,
    cleanup_iteration,
)
from ccbridge.core.event_bus import EventBus
from ccbridge.core.events import (
    CCBridgeEvent,
    CodexThinkingEvent,
    ContextBuiltEvent,
    ErrorEvent,
    IssueSummary,
    IterationCompleteEvent,
    StartedEvent,
    VerdictEvent,
    WarningEvent,
)
from ccbridge.core.lockfile import CCBridgeLock
from ccbridge.core.state import (
    CurrentIteration,
    State,
    clear_iteration,
    load_state,
    save_state,
)
from ccbridge.core.verdict import (
    ValidatedVerdict,
    Verdict,
    validate_semantics,
)
from ccbridge.runners.codex_runner import (
    CodexRunnerError,
    CodexRunResult,
    run_codex,
)

logger = logging.getLogger(__name__)


DEFAULT_MAX_ITERATIONS = 3
TERMINAL_VERDICTS = frozenset({"pass", "needs_human", "skipped", "error"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditPersistenceError(OSError):
    """Raised when ``audit_log.append`` fails inside ``_emit``.

    Subclasses :class:`OSError` so that callers who wrap ``run_audit``
    with ``except OSError:`` still catch it. The :func:`_run_loop`
    catch is narrow on this class specifically — other ``OSError``
    sources (git subprocess in ``build_context``, ``state.json``
    atomic write, snapshot cleanup) propagate as the real failures
    they are, and get persisted normally because the audit log itself
    is still writable.

    See ADR-002 §Consequences and the audit follow-up that distinguished
    this from a blanket ``OSError`` catch.
    """


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestratorOutcome:
    """Terminal state reported to the caller (CLI / Stop hook)."""

    run_uuid: str
    final_verdict: str
    iterations_used: int
    duration_sec: float


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_audit(
    *,
    project_dir: Path,
    ccbridge_dir: Path,
    bus: EventBus,
    run_uuid: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    rules_paths: tuple[Path, ...] = (),
    project_name: str = "untitled",
    project_id: str = "",
    max_diff_lines: int = 2000,
    min_diff_lines: int = 0,
) -> OrchestratorOutcome:
    """Run one full audit cycle.

    The function is intentionally single-purpose: it does not load the
    Config or Identity (callers do that and pass the relevant fields).
    This keeps unit-testability sharp — the test only needs a tmp_path
    and a stubbed ``run_codex``.
    """
    run_id = run_uuid or str(uuid.uuid4())
    started_wall = time.monotonic()

    audit_log = AuditLog(ccbridge_dir / "audit.jsonl")
    state_path = ccbridge_dir / "state.json"
    lockfile_path = ccbridge_dir / "lockfile"

    with CCBridgeLock(lockfile_path, run_uuid=run_id) as lock:
        if lock.recovered_stale:
            _emit(
                bus,
                audit_log,
                WarningEvent(
                    run_uuid=run_id,
                    message="recovered_stale_lock",
                    context={"path": str(lockfile_path)},
                ),
            )

        outcome = _run_loop(
            project_dir=project_dir,
            ccbridge_dir=ccbridge_dir,
            audit_log=audit_log,
            state_path=state_path,
            bus=bus,
            run_uuid=run_id,
            max_iterations=max_iterations,
            rules_paths=rules_paths,
            project_name=project_name,
            project_id=project_id,
            max_diff_lines=max_diff_lines,
            min_diff_lines=min_diff_lines,
            started_wall=started_wall,
        )

    return outcome


# ---------------------------------------------------------------------------
# Inner loop
# ---------------------------------------------------------------------------


def _run_loop(
    *,
    project_dir: Path,
    ccbridge_dir: Path,
    audit_log: AuditLog,
    state_path: Path,
    bus: EventBus,
    run_uuid: str,
    max_iterations: int,
    rules_paths: tuple[Path, ...],
    project_name: str,
    project_id: str,
    max_diff_lines: int,
    min_diff_lines: int,
    started_wall: float,
) -> OrchestratorOutcome:
    final_verdict = "error"
    iterations_used = 0
    recent_audits: list[VerdictEvent] = []

    iteration = 0
    started_emitted = False
    audit_failure: AuditPersistenceError | None = None

    try:
        while iteration < max_iterations:
            iteration += 1
            iterations_used = iteration
            iteration_id = f"{run_uuid}-{iteration}"

            try:
                ctx = build_context(
                    project_dir=project_dir,
                    ccbridge_dir=ccbridge_dir,
                    iteration_id=iteration_id,
                    run_uuid=run_uuid,
                    rules_paths=rules_paths,
                    max_diff_lines=max_diff_lines,
                    min_diff_lines=min_diff_lines,
                    recent_audits=recent_audits,
                )
            except ContextTooLargeError as exc:
                _emit(
                    bus,
                    audit_log,
                    ErrorEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        error_type="diff_too_large",
                        message=str(exc),
                    ),
                )
                final_verdict = "error"
                break

            if isinstance(ctx, ContextSkipped):
                _emit(
                    bus,
                    audit_log,
                    WarningEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        message=f"diff_skipped:{ctx.reason}",
                        context={"detail": ctx.detail},
                    ),
                )
                final_verdict = "skipped"
                # Make sure we record at least one VerdictEvent so audit
                # consumers see the terminal state, not just a warning.
                _emit(
                    bus,
                    audit_log,
                    VerdictEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        verdict="skipped",
                        summary=f"diff_skipped:{ctx.reason}",
                        issues=IssueSummary(),
                        cost_usd=0.0,
                        duration_sec=0.0,
                        verdict_confidence=1.0,
                        issues_completeness=1.0,
                    ),
                )
                break

            if not started_emitted:
                _emit(
                    bus,
                    audit_log,
                    StartedEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        project_name=project_name,
                        project_id=project_id,
                        iteration_count=iteration,
                        max_iterations=max_iterations,
                    ),
                )
                started_emitted = True

            _emit(
                bus,
                audit_log,
                ContextBuiltEvent(
                    run_uuid=run_uuid,
                    iteration_id=iteration_id,
                    diff_lines=ctx.diff_lines,
                    files_count=len(ctx.diff_files),
                    rules_count=ctx.rules_count,
                    context_level="medium",
                    cache_hit=ctx.cache_hit,
                ),
            )

            _save_inflight_state(
                state_path,
                iteration_id=iteration_id,
                iteration_count=iteration,
                max_iterations=max_iterations,
            )

            _emit(
                bus,
                audit_log,
                CodexThinkingEvent(
                    run_uuid=run_uuid, iteration_id=iteration_id
                ),
            )

            iter_start = time.monotonic()
            try:
                codex_result = run_codex(prompt=ctx.prompt, cwd=project_dir)
            except CodexRunnerError as exc:
                _emit(
                    bus,
                    audit_log,
                    ErrorEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        error_type="codex_runner",
                        message=str(exc),
                    ),
                )
                final_verdict = "error"
                cleanup_iteration(ctx.snapshot_dir)
                break

            duration = time.monotonic() - iter_start

            try:
                validated = _validate_codex_result(codex_result, ctx)
            except ValidationError as exc:
                _emit(
                    bus,
                    audit_log,
                    ErrorEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        error_type="verdict_invalid",
                        message=str(exc),
                    ),
                )
                final_verdict = "error"
                cleanup_iteration(ctx.snapshot_dir)
                break

            if validated.warnings:
                _emit(
                    bus,
                    audit_log,
                    WarningEvent(
                        run_uuid=run_uuid,
                        iteration_id=iteration_id,
                        message="semantic_validation_warnings",
                        context={
                            "dropped": len(validated.warnings),
                            "reasons": sorted(
                                {w.reason for w in validated.warnings}
                            ),
                        },
                    ),
                )

            verdict_event = VerdictEvent(
                run_uuid=run_uuid,
                iteration_id=iteration_id,
                verdict=validated.effective_verdict,
                summary=validated.verdict.summary,
                issues=_summarise_issues(validated.verdict),
                cost_usd=0.0,
                duration_sec=duration,
                verdict_confidence=validated.verdict.verdict_confidence,
                issues_completeness=validated.effective_completeness,
            )
            _emit(bus, audit_log, verdict_event)
            recent_audits.append(verdict_event)
            cleanup_iteration(ctx.snapshot_dir)

            if validated.effective_verdict in TERMINAL_VERDICTS:
                final_verdict = validated.effective_verdict
                break
            # Otherwise we keep going (verdict == "fail") for another round.
            final_verdict = validated.effective_verdict
        else:
            # Loop fell through max_iterations without a terminal break.
            # By contract three back-to-back fails escalate to needs_human.
            final_verdict = "needs_human"
    except AuditPersistenceError as exc:
        # audit_log.append itself failed. Per ADR-002 §Consequences and
        # the audit finding #5 fixup: we do NOT try to record the failure
        # in the broken audit.jsonl sink. ErrorEvent goes to the bus only;
        # renderers / future listeners still see what happened.
        #
        # NB: this catch is narrow on AuditPersistenceError (subclass of
        # OSError) precisely so unrelated OSErrors — from build_context's
        # git subprocess, state.json atomic write, snapshot cleanup —
        # propagate as their own failures and reach the broader handler
        # below, which CAN persist them normally because audit is fine.
        audit_failure = exc
        logger.exception("audit_log persistence failure")
        final_verdict = "error"
        _emit_bus_only(
            bus,
            ErrorEvent(
                run_uuid=run_uuid,
                error_type="audit_persistence",
                message=str(exc),
            ),
        )
    except OSError as exc:
        # Operational failure that is NOT audit-side: git subprocess in
        # build_context, state.json write, snapshot cleanup, etc. Audit
        # log is still writable, so we persist the real ErrorEvent
        # there; the loop-level final IterationCompleteEvent below also
        # goes to audit normally.
        logger.exception("orchestrator I/O failure (non-audit)")
        final_verdict = "error"
        try:
            _emit(
                bus,
                audit_log,
                ErrorEvent(
                    run_uuid=run_uuid,
                    error_type="orchestrator_io",
                    message=str(exc),
                ),
            )
        except AuditPersistenceError as audit_exc:
            # Defensive: in the unlikely event that the persistence
            # layer also fails right after the operational failure,
            # demote to bus-only to avoid losing both signals.
            audit_failure = audit_exc
            logger.exception(
                "audit_log persistence failure during error reporting"
            )
            _emit_bus_only(
                bus,
                ErrorEvent(
                    run_uuid=run_uuid,
                    error_type="audit_persistence",
                    message=str(audit_exc),
                ),
            )
    finally:
        # Clear in-flight state regardless of how we exited. Suppress
        # secondary I/O errors from clear_iteration itself if the disk
        # is wedged — primary outcome is already error.
        try:
            clear_iteration(state_path)
        except OSError:
            logger.exception("clear_iteration failed; state may be stale")

    # Final IterationCompleteEvent. If the audit log is already known
    # broken, do not retry it through audit_log — bus only.
    final_event = IterationCompleteEvent(
        run_uuid=run_uuid,
        iteration_id=f"{run_uuid}-{iterations_used}",
        final_verdict=_coerce_event_verdict(final_verdict),
        iterations_used=iterations_used,
        total_cost_usd=0.0,
        duration_sec=time.monotonic() - started_wall,
    )
    if audit_failure is not None:
        _emit_bus_only(bus, final_event)
    else:
        try:
            _emit(bus, audit_log, final_event)
        except AuditPersistenceError as exc:
            logger.exception("final IterationCompleteEvent persistence failure")
            final_verdict = "error"
            _emit_bus_only(
                bus,
                ErrorEvent(
                    run_uuid=run_uuid,
                    error_type="audit_persistence",
                    message=str(exc),
                ),
            )

    return OrchestratorOutcome(
        run_uuid=run_uuid,
        final_verdict=final_verdict,
        iterations_used=iterations_used,
        duration_sec=time.monotonic() - started_wall,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(bus: EventBus, audit_log: AuditLog, event: CCBridgeEvent) -> None:
    """Atomically persist event to audit.jsonl, then publish to the bus.

    Raises:
        AuditPersistenceError: if audit_log.append fails. Wraps the
            underlying OSError so :func:`_run_loop` can catch
            persistence failures specifically without swallowing
            unrelated ``OSError`` from other operations in the loop
            body. ADR-002 §Consequences.

    bus.emit is best-effort: listener exceptions are caught inside
    EventBus and logged, never propagated.
    """
    try:
        audit_log.append(event)
    except OSError as exc:
        raise AuditPersistenceError(
            f"audit_log.append failed for {type(event).__name__}: {exc}"
        ) from exc
    bus.emit(event)


def _emit_bus_only(bus: EventBus, event: CCBridgeEvent) -> None:
    """Publish to bus without touching the audit log.

    Used after an audit_log persistence failure: we cannot record the
    failure in the broken sink, but renderers / future listeners still
    deserve to know about it. Per ADR-002 §Consequences and audit
    finding #5 fixup.
    """
    bus.emit(event)


def _summarise_issues(verdict: Verdict) -> IssueSummary:
    counts = {"critical": 0, "major": 0, "minor": 0, "info": 0}
    for issue in verdict.issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return IssueSummary(**counts)


def _coerce_event_verdict(raw: str) -> Any:
    """Map orchestrator's wider string set to VerdictEvent's Literal.

    Orchestrator may produce ``error`` and ``skipped``, which are valid
    on VerdictEvent's ``final_verdict`` field but not on ``verdict``.
    """
    return raw


def _save_inflight_state(
    state_path: Path,
    *,
    iteration_id: str,
    iteration_count: int,
    max_iterations: int,
) -> None:
    existing = None
    try:
        existing = load_state(state_path)
    except ValueError:
        existing = None

    schema = existing.schema_version if existing is not None else 1
    save_state(
        state_path,
        State(
            schema_version=schema,
            current_iteration=CurrentIteration(
                id=iteration_id,
                started_at=datetime.now(UTC),
                iteration_count=iteration_count,
                max_iterations=max_iterations,
            ),
        ),
    )


def _validate_codex_result(
    result: CodexRunResult, ctx: BuiltContext
) -> ValidatedVerdict:
    """Pydantic-parse the Codex payload and run semantic validation.

    Pydantic raises ``ValidationError`` on shape/sycophancy violations;
    semantic validation never raises, only drops issues and downgrades.
    """
    verdict = Verdict.model_validate(result.parsed)
    return validate_semantics(
        verdict,
        diff_files=set(ctx.diff_files),
        file_line_counts=ctx.file_line_counts,
        known_rule_ids=set(ctx.known_rule_ids) if ctx.known_rule_ids else None,
    )


# Keep `replace` available — orchestrator uses it via _save_inflight_state's
# load_state result; explicit re-export prevents an unused-import warning if
# we later refactor _save_inflight_state to a pure derive helper.
_ = replace


__all__ = ("AuditPersistenceError", "OrchestratorOutcome", "run_audit")
