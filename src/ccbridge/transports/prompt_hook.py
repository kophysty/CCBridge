"""Claude Code UserPromptSubmit hook entry point.

Invoked by Claude Code when the user submits a prompt. JSON on stdin,
empty stdout (we are not making a decision), diagnostics on stderr.

Purpose: detect a user-typed ``[skip-review]`` marker and record an
ephemeral hint in ``<project>/.ccbridge/skip-review.json`` so the Stop
hook for the same turn can short-circuit the audit.

Why a separate hook (not Stop): the marker must come from the *user*'s
typed prompt. Claude itself can self-write transcript content but can
NOT edit the user's UserPromptSubmit payload. This is the security
boundary that lets us trust ``[skip-review]`` as a user opt-out rather
than self-bypass.

Hard contract:

* stdout is reserved (must stay empty). We never produce decision JSON
  here — UserPromptSubmit doesn't have a stop/continue contract.
* Diagnostics — including hook errors, fail-open paths — go to stderr.
* The hook is **fail-open**: any internal exception (malformed input,
  invalid project root, write failure, etc.) results in empty stdout
  and exit 0. We never wedge a Claude session because CCBridge itself
  misbehaved.
* Marker file is written **only** when a valid user marker is detected
  AND we have a usable session_id AND a writable project dir.

Marker matching: substring match between ``payload.prompt.casefold()``
and ``skip_marker.casefold()``. No regex. Default marker is
``[skip-review]``. Case-insensitive: ``[Skip-Review]``,
``[SKIP-REVIEW]`` all match.

UserPromptSubmit input schema (per Claude Code docs):

    {
        "session_id": str,
        "transcript_path": str,
        "cwd": str,
        "permission_mode": str,
        "hook_event_name": "UserPromptSubmit",
        "prompt": str
    }

Source: https://code.claude.com/docs/en/hooks (UserPromptSubmit event).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets as _secrets
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


CCBRIDGE_DIR_NAME = ".ccbridge"
SKIP_MARKER_FILENAME = "skip-review.json"
DEFAULT_SKIP_MARKER = "[skip-review]"
EXPECTED_HOOK_EVENT_NAME = "UserPromptSubmit"
SECRET_FILENAME = "skip-review.secret"
SECRET_BYTES = 32  # 256-bit HMAC key


def prompt_hook_main() -> int:
    """Read JSON from stdin, optionally write a skip-review marker.

    Returns 0 always. Stdout is always empty. Errors go to stderr.
    """
    try:
        raw = sys.stdin.read()
    except Exception as exc:  # pragma: no cover — stdin reads almost never raise
        _emit_diagnostic(f"failed to read stdin: {exc}")
        return 0

    payload = _parse_input(raw)
    if payload is None:
        return 0

    # Sanity check: this hook is wired to UserPromptSubmit only. If the
    # input claims a different event_name, we are mis-routed; refuse to
    # record any marker so that a misconfigured Stop or PostToolUse hook
    # cannot accidentally write a skip-marker. Missing field also fails
    # closed for the same reason.
    if payload.get("hook_event_name") != EXPECTED_HOOK_EVENT_NAME:
        _emit_diagnostic(
            "hook_event_name is not 'UserPromptSubmit'; refusing to "
            "record skip-marker (likely misconfigured hook)"
        )
        return 0

    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        _emit_diagnostic("missing or non-string 'prompt' field; ignoring")
        return 0

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        _emit_diagnostic("missing 'session_id'; cannot record marker")
        return 0

    project_dir = _resolve_project_dir(payload)
    if project_dir is None:
        return 0

    # Read [review] skip_marker from config.toml. Falls back to default
    # `[skip-review]` if config is missing/malformed (fail-open: hook
    # must NEVER crash Claude regardless of project state).
    skip_marker = _resolve_skip_marker(project_dir)

    if skip_marker.casefold() not in prompt.casefold():
        # No marker → nothing to record.
        return 0

    try:
        _write_marker_file(
            project_dir=project_dir,
            session_id=session_id,
            transcript_path=payload.get("transcript_path", ""),
            cwd=payload.get("cwd", ""),
            marker=skip_marker,
        )
    except OSError as exc:
        _emit_diagnostic(f"failed to write skip-review marker: {exc}")
        return 0
    except Exception as exc:  # pragma: no cover — defensive
        _emit_diagnostic(f"unexpected error writing marker: {exc}")
        logger.exception("prompt_hook: unexpected write error")
        return 0

    return 0


def _resolve_skip_marker(project_dir: Path) -> str:
    """Read [review] skip_marker from config.toml; default if absent.

    Fail-open: any error reading/parsing config → return the built-in
    default. This must NEVER crash the hook (the hook is the only thing
    standing between Claude and a halted session).
    """
    try:
        # Local import: keeps prompt_hook import cheap when config isn't
        # needed (e.g. for unit tests that monkeypatch this function).
        from ccbridge.core.config import load_config

        config = load_config(project_dir=project_dir)
        marker = config.review.skip_marker
        if isinstance(marker, str) and marker:
            return marker
        _emit_diagnostic(
            "config.review.skip_marker is empty/non-str; using default"
        )
        return DEFAULT_SKIP_MARKER
    except Exception as exc:
        _emit_diagnostic(f"could not load config; using default marker: {exc}")
        return DEFAULT_SKIP_MARKER


# ---------------------------------------------------------------------------
# Input parsing / project resolution
# ---------------------------------------------------------------------------


def _parse_input(raw: str) -> dict[str, Any] | None:
    """Parse stdin payload. Returns None on malformed input."""
    text = raw.strip()
    if not text:
        _emit_diagnostic("empty stdin")
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        _emit_diagnostic(f"invalid JSON on stdin: {exc}")
        return None
    if not isinstance(loaded, dict):
        _emit_diagnostic("stdin JSON is not an object")
        return None
    return loaded


def _resolve_project_dir(payload: dict[str, Any]) -> Path | None:
    """CLAUDE_PROJECT_DIR primary; ``cwd`` from input as fallback.

    Validates that the resulting directory exists. Returns None when
    neither source is usable (fail-open).
    """
    candidate: str | None = os.environ.get("CLAUDE_PROJECT_DIR")
    if not candidate:
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            candidate = cwd

    if not candidate:
        _emit_diagnostic(
            "CLAUDE_PROJECT_DIR unset and no cwd in input; "
            "cannot locate .ccbridge"
        )
        return None

    try:
        resolved = Path(candidate).resolve()
    except OSError as exc:
        _emit_diagnostic(f"cannot resolve project dir: {exc}")
        return None

    if not resolved.is_dir():
        _emit_diagnostic(
            f"project dir does not exist or is not a directory: {resolved}"
        )
        return None

    return resolved


# ---------------------------------------------------------------------------
# User-home secret + HMAC signature (skip-review trust boundary)
# ---------------------------------------------------------------------------
# Why a secret outside the project workspace:
#
# Without it, any process that can write to .ccbridge/ can forge a
# skip-review marker (matching session_id + transcript_path) and bypass
# the audit. That includes a compromised test/build dependency in the
# project, a CI runner with workspace write-access, or an editor plugin.
#
# Storing the HMAC key in the user's home (``~/.ccbridge/skip-review.secret``,
# mode 0600 on POSIX) raises the bar: an attacker now needs read-access
# to user home, not just project workspace. Full account compromise is
# out of scope for v0.1's threat model.
#
# Stop hook re-derives the HMAC and rejects markers without a valid
# signature. Marker file is the only handoff channel; secret never
# leaves the user-home file.


def _user_secret_path() -> Path:
    """Path to the user-home secret file (~/.ccbridge/skip-review.secret).

    Uses ``Path.home()`` so the location matches platformdirs' user dir
    only loosely — we want a single, predictable ``~/.ccbridge/`` for
    both the secret and the optional global config. Stop hook uses the
    same function so they always agree.
    """
    return Path.home() / CCBRIDGE_DIR_NAME / SECRET_FILENAME


def _get_or_create_user_secret() -> bytes:
    """Read the user-home secret, generating one if absent.

    Returns the raw 32-byte secret. Caller uses it as an HMAC-SHA256 key.
    File on disk stores hex (64 chars + newline) for human-readability.

    Generation is atomic via tempfile + os.replace; subsequent calls
    read the existing file. POSIX permissions are restricted to 0600
    (owner read/write only). On Windows, file ACLs from the user's
    home directory apply (CreateFile inherits parent dir's DACL).
    """
    secret_path = _user_secret_path()
    if secret_path.exists():
        try:
            text = secret_path.read_text(encoding="utf-8").strip()
            return bytes.fromhex(text)
        except (OSError, ValueError) as exc:
            # Corrupted/unreadable — regenerate. Better to invalidate
            # any in-flight markers than crash here (fail-open spirit).
            _emit_diagnostic(
                f"secret file unreadable, regenerating: {exc}"
            )

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    raw = _secrets.token_bytes(SECRET_BYTES)
    hex_text = raw.hex()

    fd, tmp_name = tempfile.mkstemp(
        prefix=".secret-", suffix=".tmp", dir=str(secret_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(hex_text + "\n")
        # Restrict to 0600 BEFORE replace so the final file is never
        # world-readable even briefly. Windows ignores st_mode bits but
        # parent directory ACLs apply.
        try:
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp_name, secret_path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return raw


def _compute_signature(
    secret: bytes,
    *,
    session_id: str,
    created_at: str,
    transcript_path: str,
    marker: str,
) -> str:
    """HMAC-SHA256 over the marker's canonicalized binding fields.

    Bound fields chosen so the signature is invalidated if any of them
    is tampered with:
      - session_id (binds marker to this Claude session)
      - created_at (prevents replay of an old signature)
      - transcript_path (cross-check vs Stop hook's own transcript_path)
      - marker (binds to the specific opt-out string)

    Use a delimiter (``\\x00``) the values cannot legally contain (paths
    are NUL-free on every supported OS) so we don't get length-extension
    ambiguity between concatenated fields.
    """
    msg = "\x00".join(
        [session_id, created_at, transcript_path, marker]
    ).encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Marker file write (atomic)
# ---------------------------------------------------------------------------


def _write_marker_file(
    *,
    project_dir: Path,
    session_id: str,
    transcript_path: str,
    cwd: str,
    marker: str,
) -> None:
    """Write ``.ccbridge/skip-review.json`` atomically.

    Schema (consumed by stop_hook):

        {
            "session_id": "<UserPromptSubmit.session_id>",
            "transcript_path": "<UserPromptSubmit.transcript_path>",
            "cwd": "<UserPromptSubmit.cwd>",
            "created_at": "<ISO8601 UTC>",
            "marker": "<configured skip_marker>",
            "reason": "user_marker",
            "signature": "<HMAC-SHA256 hex of binding fields>"
        }
    """
    ccbridge_dir = project_dir / CCBRIDGE_DIR_NAME
    ccbridge_dir.mkdir(parents=True, exist_ok=True)

    target = ccbridge_dir / SKIP_MARKER_FILENAME
    created_at = datetime.now(UTC).isoformat()

    secret = _get_or_create_user_secret()
    signature = _compute_signature(
        secret,
        session_id=session_id,
        created_at=created_at,
        transcript_path=transcript_path,
        marker=marker,
    )

    data = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "created_at": created_at,
        "marker": marker,
        "reason": "user_marker",
        "signature": signature,
    }

    serialized = json.dumps(data, ensure_ascii=False, indent=2)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".skip-review-", suffix=".tmp", dir=str(ccbridge_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------


def _emit_diagnostic(message: str) -> None:
    """Write a diagnostic message to stderr. Plain text; never logs
    environment variables or secrets.
    """
    sys.stderr.write(f"[ccbridge prompt_hook] {message}\n")
    sys.stderr.flush()


__all__ = ("prompt_hook_main",)
