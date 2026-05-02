"""Live tail for ``audit.jsonl`` — second-terminal renderer.

Reads ``audit.jsonl`` directly from disk (NOT via in-process EventBus)
and feeds parsed events to a :class:`Renderer`. Designed to be invoked
as a separate process (``ccbridge audit watch``) running alongside an
``audit run`` in another terminal.

Per ADR-002, this transport is **read-only**: it never writes to
``audit.jsonl`` or any other persisted state. It only renders.

Behaviour:

* **Tail-from-end by default.** If the file already has events when
  the watcher starts, they are skipped — the user wants to see what's
  happening NOW, not the full history. Pass ``from_start=True`` for
  history mode (used by ``ccbridge audit list`` style callers).
* **Wait for the file.** If ``audit.jsonl`` doesn't exist yet (the
  user started the watcher first), poll for it.
* **Tolerant to rotation / truncation.** If the file disappears
  mid-watch, poll for re-creation and reset position. If the file
  shrinks (rewritten), reset to the start of the new content.
* **Tolerant to torn writes.** Read only complete lines (terminated
  by ``\\n``); a torn final line is held until the rest arrives. A
  malformed *complete* line is logged at WARNING and skipped — does
  not crash the watcher.
* **Bounded for tests.** ``stop_after_events`` and ``max_iterations``
  let tests run deterministically without hanging.

Schema is read via :func:`ccbridge.core.events.parse_event` — the
exact same code path the writer uses on its own output. No
duplication.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ccbridge.core.events import CCBridgeEvent, parse_event
from ccbridge.renderers.base import Renderer

logger = logging.getLogger(__name__)


DEFAULT_POLL_INTERVAL_SEC = 0.5


def watch_audit_log(
    *,
    audit_path: Path,
    renderer: Renderer,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    from_start: bool = False,
    stop_after_events: int | None = None,
    max_iterations: int | None = None,
) -> int:
    """Tail ``audit_path``, render every parsed event.

    Parameters
    ----------
    audit_path
        Path to ``audit.jsonl``. May not exist yet — we wait.
    renderer
        Where to send parsed events. Any callable conforming to
        :class:`ccbridge.renderers.base.Renderer`.
    poll_interval_sec
        How long to sleep between filesystem polls. Default 0.5s
        is fine for human latency; tests pass small values.
    from_start
        If True, emit existing events before tailing. Default False
        (tail-from-end).
    stop_after_events
        If set, return as soon as this many events have been rendered.
        Tests use this for determinism. Production callers leave it
        ``None``.
    max_iterations
        If set, return after this many polling cycles regardless of
        events seen. Belt-and-suspenders bound for tests.

    Returns
    -------
    int
        Count of events successfully rendered.
    """
    rendered = 0
    # ``pos`` is the read offset we resume from; ``initialized`` flips
    # to True once we've decided whether to skip pre-existing history
    # (tail-from-end) or render it (from_start).
    pos = 0
    pending_partial = ""
    iteration = 0
    initialized = from_start  # if from_start=True, no skip; pos stays 0.

    while True:
        if max_iterations is not None and iteration >= max_iterations:
            return rendered
        iteration += 1

        if not audit_path.exists():
            time.sleep(poll_interval_sec)
            # File doesn't exist yet — once it appears, behave as a
            # fresh (empty) file: pos=0 from start of new content.
            # Reset state but DO leave initialized as-is (we still
            # want to emit history-from-zero if from_start=True, or
            # tail from zero in tail mode — either way nothing to skip
            # because the file is brand new).
            pos = 0
            pending_partial = ""
            initialized = True
            continue

        try:
            current_size = audit_path.stat().st_size
        except OSError:
            time.sleep(poll_interval_sec)
            continue

        # First time we see the file: in tail-from-end mode jump past
        # whatever history exists. In from_start mode (or for a file
        # that was created after watcher started, current_size==0)
        # we naturally start at 0.
        if not initialized:
            if current_size > 0:
                pos = current_size
            initialized = True
            time.sleep(poll_interval_sec)
            continue

        if current_size < pos:
            # File shrank — rotated or truncated. Reset to start of
            # the new content.
            pos = 0
            pending_partial = ""

        if current_size == pos:
            time.sleep(poll_interval_sec)
            continue

        new_chunk, pos = _read_from(audit_path, pos)
        if new_chunk is None:
            time.sleep(poll_interval_sec)
            continue

        text = pending_partial + new_chunk
        lines, pending_partial = _split_complete_lines(text)

        for line in lines:
            event = _try_parse_line(line)
            if event is None:
                continue
            try:
                renderer(event)
            except Exception:
                logger.exception(
                    "audit_watch: renderer raised on event %s",
                    type(event).__name__,
                )
                continue
            rendered += 1
            if stop_after_events is not None and rendered >= stop_after_events:
                return rendered


def _read_from(path: Path, pos: int) -> tuple[str | None, int]:
    """Read bytes starting at ``pos`` to EOF. Decode as UTF-8.

    Returns ``(text, new_pos)``. On I/O error returns ``(None, pos)``.
    """
    try:
        with path.open("rb") as f:
            f.seek(pos)
            chunk = f.read()
    except OSError:
        return None, pos

    try:
        decoded = chunk.decode("utf-8")
    except UnicodeDecodeError:
        # Defensive: if a multibyte char straddles our chunk boundary,
        # back off one byte until decode succeeds. In practice writers
        # write whole lines, so this rarely fires.
        for back in range(1, 4):
            try:
                decoded = chunk[:-back].decode("utf-8")
                return decoded, pos + len(chunk) - back
            except UnicodeDecodeError:
                continue
        return None, pos

    return decoded, pos + len(chunk)


def _split_complete_lines(text: str) -> tuple[list[str], str]:
    """Split ``text`` on ``\\n``. Return (complete_lines, trailing_partial).

    Trailing data without a final newline is treated as a partial line
    and held back until the next read brings the rest.
    """
    if "\n" not in text:
        return [], text
    parts = text.split("\n")
    partial = parts[-1]
    complete = parts[:-1]
    return complete, partial


def _try_parse_line(line: str) -> CCBridgeEvent | None:
    """Parse one JSON line into an event, or return None on any failure.

    Logs at WARNING for diagnosability but never raises.

    Recovery for torn writes: if the line as a whole isn't valid JSON
    but contains a balanced JSON object somewhere inside it, try to
    parse that. This handles the realistic crash scenario where the
    previous run died before writing its trailing ``\\n``, and the
    next append concatenated against it on the same physical line.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
        return _to_event(data)
    except json.JSONDecodeError:
        pass  # try recovery below

    recovered = _last_balanced_object(stripped)
    if recovered is not None:
        try:
            data = json.loads(recovered)
            event = _to_event(data)
            if event is not None:
                logger.warning(
                    "audit_watch: recovered event from torn-write line"
                )
                return event
        except json.JSONDecodeError:
            pass

    logger.warning(
        "audit_watch: skipping unparseable line (%d bytes)", len(stripped)
    )
    return None


def _to_event(data: object) -> CCBridgeEvent | None:
    """Wrap parse_event so any ValueError becomes None + log."""
    if not isinstance(data, dict):
        return None
    try:
        return parse_event(data)
    except ValueError as exc:
        logger.warning("audit_watch: skipping unparseable event: %s", exc)
        return None


def _last_balanced_object(text: str) -> str | None:
    """Return the substring of the *last* balanced ``{...}`` JSON object
    in ``text``, or ``None`` if none can be located.

    Used as torn-write recovery: the previous run may have died after
    writing its leading ``{...`` but before the closing brace +
    newline, and the next append got concatenated onto the same
    physical line. The new run's full object is the *last* balanced
    ``{...}`` in the resulting line.

    Algorithm: scan for every `{` start, attempt to find a balanced
    closing `}` starting from there. Keep the rightmost successful
    match. Respects string/escape so quoted braces don't confuse us.
    """
    last_span: tuple[int, int] | None = None

    for start_idx, ch in enumerate(text):
        if ch != "{":
            continue
        end_idx = _find_balanced_close(text, start_idx)
        if end_idx is not None:
            last_span = (start_idx, end_idx + 1)

    if last_span is None:
        return None
    return text[last_span[0] : last_span[1]]


def _find_balanced_close(text: str, open_idx: int) -> int | None:
    """Starting from ``text[open_idx] == '{'``, find the matching ``}``
    index. Respects JSON string-escaping. Returns None if no balanced
    close exists (e.g. truncated input).
    """
    depth = 0
    in_string = False
    escape_next = False

    for idx in range(open_idx, len(text)):
        ch = text[idx]
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
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx

    return None


__all__ = ("watch_audit_log",)
