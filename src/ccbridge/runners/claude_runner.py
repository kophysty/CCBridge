"""Subprocess wrapper around the `claude` CLI.

We invoke Claude Code in non-interactive mode::

    claude --print --output-format json <prompt>

Claude prints a single JSON document to stdout and exits 0 on success.
Anything else — non-zero exit, malformed JSON, missing executable,
timeout — is surfaced as a single :class:`ClaudeRunnerError` with
enough context for the orchestrator to decide what to do.

Design notes:

* We do NOT manage API keys here. Claude reads ``ANTHROPIC_API_KEY``
  from the inherited environment (see ARCHITECTURE.md §6.1). Callers
  who want to override env can pass ``env=`` explicitly.
* We do NOT retry. Retry policy is a runner-level concern in
  :mod:`codex_runner` (where 429s are expected); ``claude`` calls in
  CCBridge are rare and short, so a single attempt with a clear error
  is the right shape.
* We do NOT shell-escape. We use ``subprocess.run`` with a list argv,
  no ``shell=True``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_EXECUTABLE = "claude"
DEFAULT_TIMEOUT_SEC = 300


# ---------------------------------------------------------------------------
# Result + error types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeRunResult:
    """Successful invocation: zero exit + parseable JSON stdout."""

    parsed: dict[str, Any]
    stdout: str
    stderr: str
    returncode: int


class ClaudeRunnerError(RuntimeError):
    """Wraps every failure path of :func:`run_claude`.

    Attributes
    ----------
    returncode
        The CLI exit code, or 0 when the failure was JSON parsing.
    stdout, stderr
        Captured streams (may be empty if the process never started).
    cause
        The original exception (TimeoutExpired, FileNotFoundError,
        json.JSONDecodeError, ...) for callers that need to branch.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cause = cause


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_claude(
    *,
    prompt: str,
    cwd: Path,
    executable: str = DEFAULT_EXECUTABLE,
    timeout: int | None = DEFAULT_TIMEOUT_SEC,
    env: dict[str, str] | None = None,
) -> ClaudeRunResult:
    """Invoke ``claude --print --output-format json <prompt>``.

    Parameters
    ----------
    prompt
        The prompt text. Passed as a positional argument to claude.
    cwd
        Working directory of the subprocess. Claude reads this to
        locate ``.claude/`` and project files.
    executable
        Path to the claude binary. Defaults to ``claude`` (looked up in
        PATH). Override for non-standard installs.
    timeout
        Per-call timeout in seconds. ``None`` means no timeout, but the
        default of 300s exists so a hung CLI doesn't hang CCBridge.
    env
        Environment for the subprocess. ``None`` (default) inherits the
        current process's environment, which is what we want so
        ``ANTHROPIC_API_KEY`` reaches claude.

    Returns
    -------
    ClaudeRunResult
        On success only.

    Raises
    ------
    ClaudeRunnerError
        On any failure: non-zero exit, missing executable, timeout, or
        unparseable JSON stdout. Use ``err.cause`` for the original
        exception when branching on failure mode is needed.
    """
    argv = [executable, "--print", "--output-format", "json", prompt]
    logger.debug("running claude: %s (cwd=%s, timeout=%s)", argv, cwd, timeout)

    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClaudeRunnerError(
            f"claude executable not found at {executable!r}",
            cause=exc,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeRunnerError(
            f"claude timed out after {timeout}s",
            cause=exc,
        ) from exc

    if completed.returncode != 0:
        raise ClaudeRunnerError(
            f"claude exited with code {completed.returncode}",
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    stdout = completed.stdout or ""
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeRunnerError(
            f"claude returned non-JSON stdout: {exc}",
            returncode=completed.returncode,
            stdout=stdout,
            stderr=completed.stderr or "",
            cause=exc,
        ) from exc

    if not isinstance(parsed, dict):
        raise ClaudeRunnerError(
            "claude JSON stdout must be an object, got "
            f"{type(parsed).__name__}",
            returncode=completed.returncode,
            stdout=stdout,
            stderr=completed.stderr or "",
        )

    return ClaudeRunResult(
        parsed=parsed,
        stdout=stdout,
        stderr=completed.stderr or "",
        returncode=completed.returncode,
    )
