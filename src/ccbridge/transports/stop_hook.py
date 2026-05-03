"""Claude Code Stop hook entry point.

Invoked by Claude Code when the assistant attempts to stop. JSON on
stdin, JSON or empty on stdout, diagnostics on stderr.

Stop hook semantics (per https://code.claude.com/docs/en/hooks):

* JSON stdout is parsed only when ``exit == 0``.
* ``{"decision": "block", "reason": "..."}`` tells Claude NOT to stop
  — it must keep working. Use this for ``verdict=fail``.
* ``{"continue": false, "stopReason": "..."}`` lets Claude stop AND
  surfaces a user-visible reason. Use this for ``needs_human``,
  ``error``, lock-busy: cases where there is no point re-prompting
  Claude (the issue is human/operational).
* Empty stdout + exit 0 → no opinion, business as usual. We use this
  for ``verdict=pass`` AND for ``verdict=skipped`` (trivial diff /
  empty diff / user [skip-review] marker — those are non-events,
  not operational problems, so no user-visible message is needed).

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

Skip-review short-circuit: if the user's last UserPromptSubmit
contained the configured skip marker (default ``[skip-review]``),
``transports/prompt_hook`` records a signed marker file under
``.ccbridge/skip-review.json``. We validate its HMAC against the
user-home secret (``~/.ccbridge/skip-review.secret``) and, on success,
consume it and return empty stdout — no audit runs. Forged markers
(missing/invalid signature, wrong session_id, future timestamp,
expired) are rejected and the audit proceeds normally.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
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
SKIP_MARKER_FILENAME = "skip-review.json"
SKIP_MARKER_TTL = timedelta(minutes=30)
SKIP_MARKER_CLOCK_SKEW = timedelta(seconds=5)  # tolerate small clock drift
CONSUMED_NONCE_FILENAME = "skip-review.consumed.jsonl"


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

    # Skip-review short-circuit: if the user wrote [skip-review] in their
    # last UserPromptSubmit and our prompt_hook recorded a marker for THIS
    # session, consume it and skip the audit entirely. Empty stdout =
    # "no opinion", Claude proceeds with the normal stop.
    if _check_skip_marker(project_dir, payload):
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
# Skip-review marker (written by prompt_hook on UserPromptSubmit)
# ---------------------------------------------------------------------------


def _check_skip_marker(project_dir: Path, payload: dict[str, Any]) -> bool:
    """Return True if a valid skip-review marker matches this Stop turn.

    Match criteria (all must hold):

    * ``.ccbridge/skip-review.json`` exists and is valid JSON.
    * ``data["session_id"]`` equals ``payload["session_id"]`` (string).
    * ``data["created_at"]`` is a parseable ISO8601 timestamp.
    * Age of marker is ≤ 30 minutes (TTL guard).
    * HMAC ``signature`` validates against the user-home secret. This
      is the structural guard that prevents a workspace-write attacker
      from forging a marker (Blocker #2): forging requires the secret
      stored in ``~/.ccbridge/skip-review.secret``, not the writable
      project workspace.

    On match: best-effort delete the marker (consume), return True.
    On expired / forged / unreadable / mismatched session: best-effort
    delete the marker (so it can't be reused) and return False so the
    normal audit path runs. Exception: mismatched session_id leaves
    the marker alone — it may legitimately belong to another turn.
    """
    skip_path = project_dir / CCBRIDGE_DIR_NAME / SKIP_MARKER_FILENAME
    if not skip_path.exists():
        return False

    input_session = payload.get("session_id")
    if not isinstance(input_session, str) or not input_session:
        # No session_id in Stop input → can't match. Don't touch the
        # marker; another Stop turn for the right session may pick it up.
        return False

    try:
        text = skip_path.read_text(encoding="utf-8")
    except OSError as exc:
        _emit_diagnostic(f"stop_hook: skip-marker unreadable: {exc}")
        return False

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _emit_diagnostic(f"stop_hook: skip-marker malformed: {exc}")
        _try_unlink(skip_path)
        return False

    if not isinstance(data, dict):
        _emit_diagnostic("stop_hook: skip-marker JSON is not an object")
        _try_unlink(skip_path)
        return False

    marker_session = data.get("session_id")
    if not isinstance(marker_session, str) or marker_session != input_session:
        # Different session — leave marker for whoever it belongs to.
        return False

    created_raw = data.get("created_at", "")
    try:
        created = datetime.fromisoformat(created_raw)
    except (TypeError, ValueError):
        _emit_diagnostic("stop_hook: skip-marker has invalid created_at")
        _try_unlink(skip_path)
        return False

    if created.tzinfo is None:
        # Treat naive timestamp as UTC for backwards-tolerance.
        created = created.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    age = now - created
    if age > SKIP_MARKER_TTL:
        # Expired — clean up but do NOT skip the audit.
        _try_unlink(skip_path)
        return False
    # Future-dated marker: created_at is later than now beyond a small
    # clock-skew tolerance. Either malicious crafting or severe drift —
    # neither safe to honour (audit Medium #6).
    if age < -SKIP_MARKER_CLOCK_SKEW:
        _emit_diagnostic(
            f"stop_hook: skip-marker timestamp is in the future "
            f"({-age.total_seconds():.0f}s ahead); rejecting"
        )
        _try_unlink(skip_path)
        return False

    # Signature validation — defense-in-depth against marker forgery
    # by a workspace-writable attacker. See module docstring + Blocker #2
    # in Discovery/logs/decisions.md (2026-05-03 entry).
    if not _verify_marker_signature(data):
        _emit_diagnostic(
            "stop_hook: skip-marker signature invalid; rejecting (will "
            "run audit normally)"
        )
        _try_unlink(skip_path)
        return False

    # Replay protection (audit-2 High, 2026-05-03): even though the
    # signature is valid, an attacker with workspace-write may have
    # snapshotted the marker file during a legitimate [skip-review]
    # turn and restored it after we consumed the original. The nonce
    # store lives in user home (outside workspace) so a workspace-only
    # attacker cannot delete the consumed-record either.
    signature = data.get("signature", "")
    created_at_iso = data.get("created_at", "")
    if isinstance(signature, str) and isinstance(created_at_iso, str):
        if not _record_signature_or_detect_replay(
            signature=signature, created_at=created_at_iso
        ):
            _emit_diagnostic(
                "stop_hook: skip-marker replay detected; rejecting"
            )
            _try_unlink(skip_path)
            return False

    # Match. Consume MUST succeed before we trust the skip — otherwise
    # the marker stays on disk and is reusable on the next Stop turn,
    # potentially bypassing audit indefinitely (audit High #5).
    if not _consume_marker(skip_path):
        return False
    return True


def _consumed_nonce_path() -> Path:
    """Path to the user-home consumed-nonce store.

    Lives next to the user-home secret, in ``~/.ccbridge/``. Keeping it
    outside the project workspace is THE point: a workspace-write
    attacker can copy a marker but cannot delete records of consumed
    signatures from user home.
    """
    return Path.home() / CCBRIDGE_DIR_NAME / CONSUMED_NONCE_FILENAME


def _record_signature_or_detect_replay(
    *, signature: str, created_at: str
) -> bool:
    """Append signature to consumed-nonce store unless it's already there.

    Returns True if we successfully recorded a fresh signature (caller
    proceeds with skip). Returns False if the same signature already
    exists in the store within its TTL — that is a replay.

    The store is JSONL so concurrent writes append cleanly; it self-
    prunes expired records on every call (TTL = SKIP_MARKER_TTL plus
    a safety margin so we never forget a signature *before* the marker
    itself would have expired).

    On any internal error (file unreadable, etc.) we fail-CLOSED:
    return False, run audit. Better one extra audit than a bypass.
    """
    try:
        path = _consumed_nonce_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        retention = SKIP_MARKER_TTL + SKIP_MARKER_CLOCK_SKEW
        now = datetime.now(UTC)
        cutoff = now - retention

        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    rec_created = rec.get("created_at", "")
                    try:
                        rec_dt = datetime.fromisoformat(rec_created)
                    except (TypeError, ValueError):
                        continue
                    if rec_dt.tzinfo is None:
                        rec_dt = rec_dt.replace(tzinfo=UTC)
                    if rec_dt < cutoff:
                        continue  # expired — drop on rewrite
                    existing.append(rec)
            except OSError as exc:
                _emit_diagnostic(
                    f"stop_hook: nonce store unreadable: {exc}"
                )
                return False

        # Replay check.
        for rec in existing:
            if rec.get("signature") == signature:
                return False

        # Append our record. We rewrite the file (with pruning applied)
        # rather than appending raw — this keeps the store bounded.
        existing.append({"signature": signature, "created_at": created_at})
        serialized = "\n".join(
            json.dumps(rec, ensure_ascii=False) for rec in existing
        ) + "\n"

        import tempfile

        fd, tmp_name = tempfile.mkstemp(
            prefix=".consumed-", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(serialized)
            os.replace(tmp_name, path)
        except OSError as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            _emit_diagnostic(f"stop_hook: nonce store write failed: {exc}")
            return False
        return True
    except Exception as exc:  # pragma: no cover — defensive
        _emit_diagnostic(f"stop_hook: nonce store error: {exc}")
        return False


def _try_unlink(path: Path) -> None:
    """Best-effort delete used during marker rejection (when audit will
    still run regardless of whether the file goes away).

    We never let a delete failure crash the hook. Worst case the marker
    stays on disk and TTL eventually invalidates it.
    """
    try:
        path.unlink()
    except OSError as exc:
        _emit_diagnostic(f"stop_hook: could not delete skip-marker: {exc}")


def _consume_marker(path: Path) -> bool:
    """Delete the marker; return True iff delete actually succeeded.

    This is the consume operation in the strict sense — only after the
    marker is gone can we safely tell the caller "skip the audit". If
    delete fails, the marker is reusable and we MUST run the audit
    normally (audit High #5 fix).
    """
    try:
        path.unlink()
    except OSError as exc:
        _emit_diagnostic(
            f"stop_hook: could not consume skip-marker, will run audit: "
            f"{exc}"
        )
        return False
    return True


def _verify_marker_signature(data: dict[str, Any]) -> bool:
    """True iff the marker's `signature` field matches the HMAC we
    re-derive from the user-home secret + binding fields.

    Returns False on:
    * missing or non-string signature
    * missing user-home secret (no possible valid signature)
    * any HMAC mismatch (constant-time compare)
    * any internal error reading the secret (fail-closed: a marker
      we can't validate is not trusted)
    """
    signature = data.get("signature")
    if not isinstance(signature, str) or len(signature) != 64:
        return False

    try:
        from ccbridge.transports.prompt_hook import (
            _compute_signature,
            _user_secret_path,
        )

        secret_path = _user_secret_path()
        if not secret_path.exists():
            return False
        try:
            secret_hex = secret_path.read_text(encoding="utf-8").strip()
            secret = bytes.fromhex(secret_hex)
        except (OSError, ValueError):
            return False

        session_id = data.get("session_id", "")
        created_at = data.get("created_at", "")
        transcript_path = data.get("transcript_path", "")
        marker = data.get("marker", "")
        if not all(
            isinstance(v, str)
            for v in (session_id, created_at, transcript_path, marker)
        ):
            return False

        expected = _compute_signature(
            secret,
            session_id=session_id,
            created_at=created_at,
            transcript_path=transcript_path,
            marker=marker,
        )
        # constant-time compare
        import hmac as _hmac

        return _hmac.compare_digest(expected, signature)
    except Exception as exc:  # pragma: no cover — defensive
        _emit_diagnostic(f"stop_hook: signature verify failed: {exc}")
        return False


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
        # Skipped is a non-event for Claude (no real diff to review or
        # below trivial-diff threshold). Empty stdout = "no opinion" —
        # Claude proceeds with the normal stop. ``continue:false`` is
        # reserved for operational problems the user must address.
        return None

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
