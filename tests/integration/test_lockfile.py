"""Integration tests for ccbridge.core.lockfile."""

from __future__ import annotations

import json
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ccbridge.core.lockfile import (
    CCBridgeLock,
    LockBusyError,
    LockCorruptError,
    LockHolder,
)


def test_acquire_creates_lock_file_with_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / ".ccbridge" / "lockfile"
    lock = CCBridgeLock(lock_path, run_uuid="run-1")

    holder = lock.acquire()
    try:
        assert lock_path.exists()
        assert holder.pid == os.getpid()
        assert holder.hostname == socket.gethostname()
        assert holder.run_uuid == "run-1"

        # File contents are JSON with the same shape.
        on_disk = json.loads(lock_path.read_text(encoding="utf-8"))
        assert on_disk["pid"] == os.getpid()
        assert on_disk["run_uuid"] == "run-1"
    finally:
        lock.release()


def test_release_removes_lock_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "lockfile"
    lock = CCBridgeLock(lock_path)

    lock.acquire()
    assert lock_path.exists()
    lock.release()
    assert not lock_path.exists()


def test_context_manager_releases_on_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "lockfile"
    with CCBridgeLock(lock_path) as lock:
        assert lock_path.exists()
        assert lock.holder is not None
    assert not lock_path.exists()


def test_context_manager_releases_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "lockfile"
    with pytest.raises(RuntimeError, match="boom"):
        with CCBridgeLock(lock_path):
            assert lock_path.exists()
            raise RuntimeError("boom")
    assert not lock_path.exists()


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock_path = tmp_path / "lockfile"
    lock = CCBridgeLock(lock_path)
    lock.acquire()
    lock.release()
    lock.release()  # second release must not raise


def test_concurrent_acquire_raises_lock_busy(tmp_path: Path) -> None:
    lock_path = tmp_path / "lockfile"
    first = CCBridgeLock(lock_path, run_uuid="first")
    first.acquire()
    try:
        second = CCBridgeLock(lock_path, run_uuid="second")
        with pytest.raises(LockBusyError) as exc_info:
            second.acquire()

        # The exception carries metadata about the holder.
        assert exc_info.value.holder.run_uuid == "first"
        assert "first" in str(exc_info.value)
    finally:
        first.release()


def test_stale_lock_is_taken_over(tmp_path: Path) -> None:
    """A lock older than `stale_after` is considered abandoned and recovered."""
    lock_path = tmp_path / "lockfile"

    # Seed the directory with an "abandoned" lock from one hour ago.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    abandoned = LockHolder(
        pid=999_999,
        hostname="dead-host",
        started_at=datetime.now(UTC) - timedelta(hours=1),
        run_uuid="abandoned",
    )
    lock_path.write_text(abandoned.to_json(), encoding="utf-8")

    fresh = CCBridgeLock(
        lock_path,
        run_uuid="fresh",
        stale_after=timedelta(minutes=30),
    )
    holder = fresh.acquire()
    try:
        assert holder.run_uuid == "fresh"
        assert fresh.recovered_stale is True
    finally:
        fresh.release()


def test_fresh_lock_is_not_stale(tmp_path: Path) -> None:
    """A lock just acquired is not eligible for takeover."""
    lock_path = tmp_path / "lockfile"
    first = CCBridgeLock(
        lock_path,
        run_uuid="first",
        stale_after=timedelta(minutes=30),
    )
    first.acquire()
    try:
        second = CCBridgeLock(
            lock_path,
            run_uuid="second",
            stale_after=timedelta(minutes=30),
        )
        with pytest.raises(LockBusyError):
            second.acquire()
        assert second.recovered_stale is False
    finally:
        first.release()


def test_corrupt_lock_raises_lock_corrupt(tmp_path: Path) -> None:
    """A garbage lock file is not auto-cleaned — caller decides."""
    lock_path = tmp_path / "lockfile"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not json {", encoding="utf-8")

    lock = CCBridgeLock(lock_path)
    with pytest.raises(LockCorruptError):
        lock.acquire()


def test_acquire_creates_parent_directory(tmp_path: Path) -> None:
    lock_path = tmp_path / "deeply" / "nested" / "dir" / "lockfile"
    assert not lock_path.parent.exists()

    with CCBridgeLock(lock_path):
        assert lock_path.exists()


def test_holder_property_none_before_acquire(tmp_path: Path) -> None:
    lock = CCBridgeLock(tmp_path / "lockfile")
    assert lock.holder is None


def test_recovered_stale_property_false_for_clean_acquire(tmp_path: Path) -> None:
    lock = CCBridgeLock(tmp_path / "lockfile")
    with lock:
        assert lock.recovered_stale is False
