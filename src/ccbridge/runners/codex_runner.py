"""Subprocess wrapper around the OpenAI ``codex`` CLI.

Codex is invoked non-interactively::

    codex exec --json <prompt>

The CLI prints a model response to stdout. In the happy path that is a
single JSON object — the Verdict. In practice the model often wraps it
in a markdown fence (`````json ... `````)
or prefixes it with prose. :func:`extract_json_payload` extracts the
first complete JSON object from such output.

Failure modes handled here (closes ARCHITECTURE.md AC-4 lenient JSON,
AC-19 network resilience):

* Non-zero exit with a 429-shaped stderr → retry with backoff. Honour
  ``Retry-After`` when present in stderr.
* Non-zero exit, non-rate-limit → fail fast.
* Unparseable stdout → one short retry, then :class:`CodexRunnerError`.
* Missing executable → :class:`CodexRunnerError`.
* Timeout → :class:`CodexRunnerError`.

Secrets: this module does not manage API keys. Codex reads
``OPENAI_API_KEY`` (or whatever is configured under ``[codex]
api_key_env``) from the inherited environment. See ARCHITECTURE.md
§6.1.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_EXECUTABLE = "codex"
DEFAULT_TIMEOUT_SEC = 600
DEFAULT_MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_MAX_JSON_RETRIES = 1
# Backoff schedule for 429 retries when no Retry-After is provided.
# Doubles roughly: 1, 4, 16 (matches ARCHITECTURE.md AC-19).
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (1, 4, 16)
JSON_RETRY_PAUSE_SEC = 1


# ---------------------------------------------------------------------------
# Result + error types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodexRunResult:
    """Successful invocation: JSON object extracted from stdout."""

    parsed: dict[str, Any]
    stdout: str
    stderr: str
    returncode: int
    retry_count: int


class CodexRunnerError(RuntimeError):
    """Wraps every non-rate-limit failure path of :func:`run_codex`."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        retry_count: int = 0,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.retry_count = retry_count
        self.cause = cause


class CodexRateLimitError(CodexRunnerError):
    """Raised when 429 retries are exhausted."""


# ---------------------------------------------------------------------------
# Lenient JSON extraction
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(
    r"```(?:json)?\s*(?P<body>.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)


def extract_json_payload(raw: str) -> dict[str, Any]:
    """Pull the first complete JSON object out of ``raw``.

    Strategy:

    1. Try to parse the whole string as JSON (the strict happy path).
    2. If a markdown fence is present, parse the fence body.
    3. Otherwise scan for the first ``{`` and walk braces until a
       balanced object is closed; parse that slice.

    Raises:
        ValueError: if no JSON object can be located or parsed.
    """
    if not raw or not raw.strip():
        raise ValueError("empty input")

    text = raw.strip()

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None

    if isinstance(loaded, dict):
        return loaded

    fence_match = _FENCE_RE.search(text)
    if fence_match is not None:
        body = fence_match.group("body").strip()
        try:
            loaded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"markdown fence body is not valid JSON: {exc}"
            ) from exc
        if not isinstance(loaded, dict):
            raise ValueError("markdown fence body is not a JSON object")
        return loaded

    span = _find_first_balanced_object(text)
    if span is None:
        raise ValueError("no JSON object found in input")

    candidate = text[span[0] : span[1]]
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"candidate JSON object failed to parse: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("first JSON value is not an object")
    return loaded


def _find_first_balanced_object(text: str) -> tuple[int, int] | None:
    """Return (start, end_exclusive) of the first balanced ``{...}`` slice.

    Respects strings (so ``{"x":"}"}`` parses correctly). Returns None if
    no balanced object can be found.
    """
    depth = 0
    start: int | None = None
    in_string = False
    escape_next = False

    for idx, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if in_string:
            if ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start is not None:
                return (start, idx + 1)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_codex(
    *,
    prompt: str,
    cwd: Path,
    executable: str = DEFAULT_EXECUTABLE,
    timeout: int | None = DEFAULT_TIMEOUT_SEC,
    env: dict[str, str] | None = None,
    max_rate_limit_retries: int = DEFAULT_MAX_RATE_LIMIT_RETRIES,
    max_json_retries: int = DEFAULT_MAX_JSON_RETRIES,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
) -> CodexRunResult:
    """Invoke ``codex exec --json <prompt>`` with retries and lenient JSON.

    Returns a :class:`CodexRunResult` only on successful invocation that
    yields a parseable JSON object. All failure paths raise
    :class:`CodexRunnerError` (or :class:`CodexRateLimitError` for
    exhausted 429 retries).
    """
    argv = [executable, "exec", "--json", prompt]
    rate_limit_retries = 0
    json_retries = 0
    last_stdout = ""
    last_stderr = ""
    last_returncode = 0

    while True:
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
            raise CodexRunnerError(
                f"codex executable not found at {executable!r}",
                cause=exc,
                retry_count=rate_limit_retries + json_retries,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CodexRunnerError(
                f"codex timed out after {timeout}s",
                cause=exc,
                retry_count=rate_limit_retries + json_retries,
            ) from exc

        last_stdout = completed.stdout or ""
        last_stderr = completed.stderr or ""
        last_returncode = completed.returncode

        if completed.returncode != 0:
            if _is_rate_limited(last_stderr):
                if rate_limit_retries >= max_rate_limit_retries:
                    raise CodexRateLimitError(
                        "codex rate-limited; retries exhausted",
                        returncode=last_returncode,
                        stdout=last_stdout,
                        stderr=last_stderr,
                        retry_count=rate_limit_retries,
                    )
                pause = _next_backoff(
                    last_stderr, rate_limit_retries, backoff_seconds
                )
                logger.warning(
                    "codex 429: sleeping %.1fs before retry %d/%d",
                    pause,
                    rate_limit_retries + 1,
                    max_rate_limit_retries,
                )
                time.sleep(pause)
                rate_limit_retries += 1
                continue

            raise CodexRunnerError(
                f"codex exited with code {last_returncode}",
                returncode=last_returncode,
                stdout=last_stdout,
                stderr=last_stderr,
                retry_count=rate_limit_retries + json_retries,
            )

        try:
            parsed = extract_json_payload(last_stdout)
        except ValueError as exc:
            if json_retries >= max_json_retries:
                raise CodexRunnerError(
                    f"codex stdout did not contain valid JSON: {exc}",
                    returncode=last_returncode,
                    stdout=last_stdout,
                    stderr=last_stderr,
                    retry_count=rate_limit_retries + json_retries,
                    cause=exc,
                ) from exc
            logger.warning(
                "codex returned unparseable JSON; retrying once (%s)", exc
            )
            time.sleep(JSON_RETRY_PAUSE_SEC)
            json_retries += 1
            continue

        return CodexRunResult(
            parsed=parsed,
            stdout=last_stdout,
            stderr=last_stderr,
            returncode=last_returncode,
            retry_count=rate_limit_retries + json_retries,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_RATE_LIMIT_RE = re.compile(r"\b429\b|rate[\s_-]?limit", re.IGNORECASE)
_RETRY_AFTER_RE = re.compile(
    r"retry[-_\s]?after\s*[:=]?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _is_rate_limited(stderr: str) -> bool:
    return bool(_RATE_LIMIT_RE.search(stderr or ""))


def _next_backoff(
    stderr: str,
    attempt: int,
    backoff_seconds: tuple[int, ...],
) -> float:
    """Compute next sleep: prefer Retry-After hint, fall back to schedule."""
    match = _RETRY_AFTER_RE.search(stderr or "")
    if match is not None:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    if backoff_seconds:
        idx = min(attempt, len(backoff_seconds) - 1)
        return float(backoff_seconds[idx])
    return 1.0
