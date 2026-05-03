"""Claude Code Stop hook entry point.

Invoked by Claude Code when the assistant attempts to stop. JSON on
stdin, JSON or empty on stdout, diagnostics on stderr.

Stop hook semantics (per https://code.claude.com/docs/en/hooks):

* JSON stdout is parsed only when ``exit == 0``.
* ``{"decision": "block", "reason": "..."}`` tells Claude NOT to stop
  — it must keep working. Use this for ``verdict=fail``.
* ``{"continue": false, "stopReason": "..."}`` lets Claude stop AND
  surfaces a user-visible reason. Use this for ``needs_human``,
  ``error``, ``skipped``, lock-busy: cases where there is no point
  re-prompting Claude (the issue is human/operational).
* Empty stdout + exit 0 → no opinion, business as usual.

Hard contract for stdout (audit finding follow-up):

* stdout is reserved STRICTLY for the JSON decision object or empty.
* No ANSI escapes, no rich formatting. ``reason`` / ``stopReason``
  are plain text bounded by ``MAX_REASON_CHARS``.
* Diagnostics — including hook errors and internal crashes — go to
  stderr only.
* The hook is **fail-open**: any internal exception (malformed input,
  invalid project root, run_audit crash, etc.) results in empty
  stdout and exit 0. We never want to wedge a Claude session because
  CCBridge itself misbehaved.

Recursion guard: ``stop_hook_active=true`` in input means we are
already inside a hook-triggered Claude turn. We must not start another
audit, otherwise the cycle never terminates.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from ccbridge.core.event_bus import EventBus
from ccbridge.core.lockfile import LockBusyError
from ccbridge.core.orchestrator import OrchestratorOutcome
from ccbridge.renderers.rich_renderer import RichRenderer
from ccbridge.transports.audit_invoker import run_audit_with_config

logger = logging.getLogger(__name__)


MAX_REASON_CHARS = 4000
CCBRIDGE_DIR_NAME = ".ccbridge"


def stop_hook_main() -> int:
    """Read JSON from stdin, run the audit, write decision JSON to stdout.

    Returns the process exit code (always 0 in current contract — we
    never want to crash Claude).
    """
    try:
        raw = sys.stdin.read()
    except Exception as exc:  # pragma: no cover — stdin reads almost never raise
        _emit_diagnostic(f"stop_hook: failed to read stdin: {exc}")
        return 0

    payload = _parse_input(raw)
    if payload is None:
        return 0

    if payload.get("stop_hook_active") is True:
        # Already inside a hook-triggered Claude turn. Skip silently.
        return 0

    project_dir = _resolve_project_dir(payload)
    if project_dir is None:
        return 0

    try:
        outcome = _run_audit_for_hook(project_dir)
    except LockBusyError as exc:
        _emit_decision(
            {
                "continue": False,
                "stopReason": _bound(
                    "CCBridge audit already running on this project "
                    f"({exc.holder.run_uuid}). Wait for it to finish or "
                    "investigate via `ccbridge status`."
                ),
            }
        )
        return 0
    except Exception as exc:
        # Never let an internal failure crash Claude. We surface the
        # error to stderr so the user can see something happened, but
        # stdout stays empty.
        _emit_diagnostic(f"stop_hook: audit failed: {exc}")
        logger.exception("stop_hook: audit raised")
        return 0

    decision = _decision_for_outcome(outcome)
    if decision is not None:
        _emit_decision(decision)
    return 0


# ---------------------------------------------------------------------------
# Input parsing / project resolution
# ---------------------------------------------------------------------------


def _parse_input(raw: str) -> dict[str, Any] | None:
    """Parse stdin payload. Returns None on malformed input (fail-open
    path).
    """
    text = raw.strip()
    if not text:
        _emit_diagnostic("stop_hook: empty stdin")
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        _emit_diagnostic(f"stop_hook: invalid JSON on stdin: {exc}")
        return None
    if not isinstance(loaded, dict):
        _emit_diagnostic("stop_hook: stdin JSON is not an object")
        return None
    return loaded


def _resolve_project_dir(payload: dict[str, Any]) -> Path | None:
    """CLAUDE_PROJECT_DIR primary; ``cwd`` from input as fallback.

    Validates that the resulting directory exists. Returns None when
    neither source is usable (fail-open: hook will exit 0 with empty
    stdout).
    """
    candidate: str | None = os.environ.get("CLAUDE_PROJECT_DIR")
    if not candidate:
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            candidate = cwd

    if not candidate:
        _emit_diagnostic(
            "stop_hook: CLAUDE_PROJECT_DIR unset and no cwd in input; "
            "skipping audit"
        )
        return None

    try:
        resolved = Path(candidate).resolve()
    except OSError as exc:
        _emit_diagnostic(f"stop_hook: cannot resolve project dir: {exc}")
        return None

    if not resolved.is_dir():
        _emit_diagnostic(
            f"stop_hook: project dir does not exist or is not a directory: "
            f"{resolved}"
        )
        return None

    return resolved


# ---------------------------------------------------------------------------
# Orchestrator invocation
# ---------------------------------------------------------------------------


def _run_audit_for_hook(project_dir: Path) -> OrchestratorOutcome:
    """Wire up an EventBus with a stderr-only RichRenderer and call
    run_audit through the shared invoker, which also reads config.toml
    + identity.json (audit Major #2 fix). ``cli_mode=False`` means
    a malformed config produces a stderr warning + defaults fallback,
    not a crash — Stop hook is fail-open.

    NB: stdout MUST stay clean for the decision JSON. RichRenderer is
    explicitly bound to stderr.
    """
    bus = EventBus()
    bus.subscribe(RichRenderer(file=sys.stderr))

    return run_audit_with_config(
        project_dir=project_dir,
        ccbridge_dir=project_dir / CCBRIDGE_DIR_NAME,
        bus=bus,
        cli_mode=False,
        run_uuid=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Decision shaping
# ---------------------------------------------------------------------------


def _decision_for_outcome(
    outcome: OrchestratorOutcome,
) -> dict[str, Any] | None:
    """Map orchestrator outcome → Claude Stop hook decision JSON.

    Returns None for ``pass`` (no opinion → empty stdout).
    """
    verdict = outcome.final_verdict

    if verdict == "pass":
        return None

    if verdict == "fail":
        reason = _bound(
            f"CCBridge review found issues that need to be fixed before "
            f"stopping. Run completed in {outcome.iterations_used} "
            f"iteration(s). Check `ccbridge audit get {outcome.run_uuid}` "
            f"for details, fix the reported issues, then continue."
        )
        return {"decision": "block", "reason": reason}

    if verdict == "needs_human":
        return {
            "continue": False,
            "stopReason": _bound(
                f"CCBridge needs human review (run {outcome.run_uuid}). "
                f"Codex was not confident enough to give a definitive "
                f"verdict after {outcome.iterations_used} iteration(s). "
                f"Inspect with `ccbridge audit get {outcome.run_uuid}`."
            ),
        }

    if verdict == "error":
        return {
            "continue": False,
            "stopReason": _bound(
                f"CCBridge encountered an operational error during review "
                f"(run {outcome.run_uuid}). Inspect with `ccbridge audit "
                f"get {outcome.run_uuid}` for the full event log."
            ),
        }

    if verdict == "skipped":
        return {
            "continue": False,
            "stopReason": _bound(
                f"CCBridge skipped review (run {outcome.run_uuid}): "
                f"empty or binary-only diff. No code changes were "
                f"detected to audit."
            ),
        }

    # Unknown verdict — defensive: treat as needs_human.
    return {
        "continue": False,
        "stopReason": _bound(
            f"CCBridge produced unknown verdict {verdict!r} for run "
            f"{outcome.run_uuid}."
        ),
    }


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------


def _emit_decision(decision: dict[str, Any]) -> None:
    """Write the decision JSON to stdout. No newline framing needed —
    Claude parses stdout as a single JSON document.
    """
    sys.stdout.write(json.dumps(decision, ensure_ascii=False))
    sys.stdout.flush()


def _emit_diagnostic(message: str) -> None:
    """Write a diagnostic message to stderr. Plain text; never logs
    environment variables or secrets.
    """
    sys.stderr.write(f"[ccbridge stop_hook] {message}\n")
    sys.stderr.flush()


def _bound(text: str, *, limit: int = MAX_REASON_CHARS) -> str:
    """Truncate ``text`` to ``limit`` characters with an ellipsis if
    needed. Strips ANSI escapes defensively.
    """
    plain = _strip_ansi(text)
    if len(plain) <= limit:
        return plain
    return plain[: limit - 3] + "..."


_ANSI_CSI_PREFIX = "\x1b["
_ANSI_OSC_PREFIX = "\x1b]"


def _strip_ansi(text: str) -> str:
    """Defensive ANSI strip. Our source strings should not contain ANSI
    in the first place, but this guarantees the contract even if a
    future caller passes a styled message in by accident.
    """
    if _ANSI_CSI_PREFIX not in text and _ANSI_OSC_PREFIX not in text:
        return text
    import re

    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07", "", text)


__all__ = ("stop_hook_main",)
