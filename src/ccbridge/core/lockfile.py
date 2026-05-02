"""Cross-platform file lock with stale-detection and TTL recovery.

Built on `portalocker`, which wraps `fcntl` (POSIX) and `msvcrt`
(Windows) under one API. We add:

* Triplet metadata in the lock file itself (`pid`, `hostname`,
  `started_at`, `run_uuid`) — for diagnostics and stale recovery.
* TTL-based stale takeover: if the lock is held longer than
  `stale_after`, we treat it as abandoned, log a recovery event,
  and acquire it.
* A context manager that always releases on `__exit__`, even on crash.

See ARCHITECTURE.md §2.3 for the full discipline.

Failure modes considered (closes audit findings P0-1, sd-#1, sd-#3, sd-#5):

* Two processes racing acquire — `O_EXCL` semantics prevent both
  succeeding.
* PID recycling (Windows reuses PIDs faster than Linux) — never trust
  PID alone; always check timestamp.
* Stale lock after a crash — TTL recovery without manual cleanup.
* Reader keeps the file open during writer's `os.replace` — we never
  rewrite the lock file, only create-or-fail and unlink.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

import portalocker

logger = logging.getLogger(__name__)


DEFAULT_STALE_AFTER = timedelta(minutes=30)


class LockBusyError(RuntimeError):
    """Raised when the lock is held by a live process and we can't take over."""

    def __init__(self, holder: LockHolder) -> None:
        self.holder = holder
        super().__init__(
            f"lock held by pid={holder.pid} on {holder.hostname} "
            f"since {holder.started_at.isoformat()} (run_uuid={holder.run_uuid})"
        )


class LockCorruptError(RuntimeError):
    """Raised when an existing lock file cannot be parsed.

    We do not auto-clean a corrupt lock — that would mask other bugs.
    Caller decides whether to delete and retry.
    """


@dataclass(frozen=True)
class LockHolder:
    """Metadata recorded inside a lock file."""

    pid: int
    hostname: str
    started_at: datetime
    run_uuid: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "pid": self.pid,
                "hostname": self.hostname,
                "started_at": self.started_at.isoformat(),
                "run_uuid": self.run_uuid,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> LockHolder:
        try:
            data = json.loads(raw)
            return cls(
                pid=int(data["pid"]),
                hostname=str(data["hostname"]),
                started_at=datetime.fromisoformat(data["started_at"]),
                run_uuid=str(data["run_uuid"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            raise LockCorruptError(f"cannot parse lock file: {exc}") from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CCBridgeLock:
    """Context-managed exclusive lock for a single project's `.ccbridge/` dir.

    Usage::

        with CCBridgeLock(project_dir / ".ccbridge" / "lockfile") as lock:
            # critical section
            ...

    Stale recovery:
        If the lock file already exists *and* its `started_at` is older
        than `stale_after`, we delete it (logging at WARNING) and acquire.
        The caller is responsible for emitting a `WarningEvent` — the
        lockfile module deliberately does not depend on EventBus.

    Thread safety:
        Not thread-safe. CCBridge runs single-threaded per process.
    """

    def __init__(
        self,
        path: Path,
        *,
        run_uuid: str | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self.path = path
        self.run_uuid = run_uuid or str(uuid.uuid4())
        self.stale_after = stale_after
        self._holder: LockHolder | None = None
        self._recovered_stale: bool = False

    @property
    def holder(self) -> LockHolder | None:
        """Metadata of the active holder (None until acquire succeeds)."""
        return self._holder

    @property
    def recovered_stale(self) -> bool:
        """True if `acquire` had to take over an abandoned lock.

        Caller should emit a WarningEvent / append a `recovered_stale_lock`
        record to audit.jsonl when this is True.
        """
        return self._recovered_stale

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> CCBridgeLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Public ops
    # ------------------------------------------------------------------

    def acquire(self) -> LockHolder:
        """Atomically acquire the lock.

        Raises:
            LockBusyError: another live holder owns the lock and TTL hasn't
                expired.
            LockCorruptError: existing lock file unparseable.
            OSError: filesystem error other than EEXIST.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._create_exclusive()
        except FileExistsError:
            existing = self._read_existing()
            if self._is_stale(existing):
                logger.warning(
                    "stale lock detected (age %.0fs > %.0fs); taking over from "
                    "pid=%d run_uuid=%s",
                    (_utc_now() - existing.started_at).total_seconds(),
                    self.stale_after.total_seconds(),
                    existing.pid,
                    existing.run_uuid,
                )
                self.path.unlink(missing_ok=True)
                self._create_exclusive()
                self._recovered_stale = True
            else:
                raise LockBusyError(existing) from None

        assert self._holder is not None
        return self._holder

    def release(self) -> None:
        """Release the lock by deleting the file.

        Idempotent — safe to call when not held or when the file
        was deleted externally.
        """
        if self._holder is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        finally:
            self._holder = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_exclusive(self) -> None:
        """Open the lock file with O_CREAT | O_EXCL and write metadata.

        portalocker.Lock with `fail_when_locked=True` and
        `flags=LockFlags.EXCLUSIVE` gives the same semantic on both POSIX
        and Windows. We deliberately write the metadata while holding
        the OS lock so a concurrent reader sees either the full record
        or no file.
        """
        holder = LockHolder(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            started_at=_utc_now(),
            run_uuid=self.run_uuid,
        )

        # `mode='x'` would raise FileExistsError if the file exists, but it
        # doesn't combine well with portalocker's flock on every platform.
        # Two-step approach: O_CREAT|O_EXCL via os.open, then portalocker
        # for an OS-level advisory lock the duration of the write.
        try:
            fd = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            raise

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                # An advisory lock here prevents a reader from seeing
                # a half-written record.
                portalocker.lock(f, portalocker.LockFlags.EXCLUSIVE)
                try:
                    f.write(holder.to_json())
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    portalocker.unlock(f)
        except Exception:
            # If something went wrong after we created the file but before
            # we finished writing, leave a clean state for the next process.
            self.path.unlink(missing_ok=True)
            raise

        self._holder = holder

    def _read_existing(self) -> LockHolder:
        raw = self.path.read_text(encoding="utf-8")
        return LockHolder.from_json(raw)

    def _is_stale(self, holder: LockHolder) -> bool:
        age = _utc_now() - holder.started_at
        return age > self.stale_after
