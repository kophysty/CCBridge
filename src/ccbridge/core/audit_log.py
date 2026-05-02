"""Append-only JSON-lines audit log.

The audit log is the **primary source of truth** for the project. The
state file (`state.json`) is a derived cache that can always be
reconstructed from the last few lines of the log. See ARCHITECTURE.md
§2.4.

Design constraints (from audit findings P0-3, sd-#7, sd-#28):

* Every record is a single line of JSON ending with `\\n`. We write
  the line in one `os.write` call so that, on POSIX, blocks under
  `PIPE_BUF` (4 KiB on Linux) are guaranteed atomic. On Windows we
  rely on the OS append semantics; a torn write can leave the last
  line malformed, and the reader must tolerate it.

* The reader is *tolerant*: malformed last lines are skipped with a
  warning. This keeps `ccbridge audit list` working even after a crash.

* Records are typed via the same `CCBridgeEvent` hierarchy used by the
  EventBus (`core.events`). The audit log is essentially the
  persisted event stream.

* `ccbridge audit watch` reads the file with seek-to-end-and-tail; we
  guarantee that complete lines will eventually appear, but we make
  no in-process delivery promise (use the EventBus for that).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

from ccbridge.core.events import CCBridgeEvent, parse_event

logger = logging.getLogger(__name__)


class AuditLog:
    """Append-only writer + tolerant reader for `audit.jsonl`.

    Construct once per project and reuse. Not thread-safe; expected to
    be used inside the orchestrator's lock-protected critical section.

    The class deliberately does *not* hold an open file handle between
    calls. Each `append` opens, writes, closes — this trades a tiny bit
    of performance for crash resilience and easier `audit watch`
    behaviour (no flush coordination needed across processes).
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    # ------------------------------------------------------------------
    # Writer
    # ------------------------------------------------------------------

    def append(self, event: CCBridgeEvent) -> None:
        """Atomically append a single event as one JSON line.

        We serialize first, then issue exactly one `os.write` of the
        complete line including the trailing `\\n`. This is atomic on
        POSIX for blocks under PIPE_BUF (4 KiB on Linux); Windows
        provides no equivalent guarantee, hence the tolerant reader.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
        encoded = line.encode("utf-8")

        # Open with O_APPEND so concurrent writers (should not happen, but
        # we are defensive) at least don't overwrite each other's lines.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(self.path, flags, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)

    # ------------------------------------------------------------------
    # Reader
    # ------------------------------------------------------------------

    def read_all(self) -> Iterator[CCBridgeEvent]:
        """Yield every parseable event from the log, in order.

        Lines that fail to parse are logged at WARNING level and skipped.
        A torn final line is the most common cause and is treated as a
        recoverable artifact, not corruption.
        """
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                stripped = raw.rstrip("\n")
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                    yield parse_event(data)
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "audit_log: skipping unparseable line %d in %s: %s",
                        line_no,
                        self.path,
                        exc,
                    )

    def read_tail(self, n: int) -> list[CCBridgeEvent]:
        """Return the last `n` parseable events.

        Reads the whole file in v0.1 — fine for audit logs under a few MB.
        For larger logs we'd seek backwards line-by-line, but that is
        a v0.2 optimisation when rotation kicks in.
        """
        if n <= 0:
            return []
        events = list(self.read_all())
        return events[-n:]

    def last(self) -> CCBridgeEvent | None:
        """Return the last parseable event, or None if log is empty/missing."""
        tail = self.read_tail(1)
        return tail[0] if tail else None

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def size_bytes(self) -> int:
        """Current size of the file. 0 if missing."""
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def line_count(self) -> int:
        """Total lines (parseable or not). 0 if missing."""
        if not self.path.exists():
            return 0
        with self.path.open("rb") as f:
            return sum(1 for _ in f)
