"""Integration tests for transports/audit_watch.

The watcher is a separate-process reader of ``audit.jsonl`` (NOT a
subscriber to the in-process EventBus — that lives only inside
``run_audit``). Contract:

* Tail from end by default (don't dump full history).
* Poll-based, KISS: configurable ``poll_interval_sec``.
* Tolerant to torn-write last line (skip until next complete line).
* Tolerant to file delete/rotate: wait for re-creation, reset
  position when the file shrinks.
* Read-only: never writes back to audit.jsonl.
* Re-uses ``parse_event`` from ``core.events`` — no schema duplication.
* Bounded loop for tests via ``stop_after_events`` / ``max_iterations``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from ccbridge.core.audit_log import AuditLog
from ccbridge.core.events import (
    CCBridgeEvent,
    IssueSummary,
    StartedEvent,
    VerdictEvent,
)
from ccbridge.renderers.silent_renderer import SilentRenderer
from ccbridge.transports.audit_watch import watch_audit_log

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _started(uuid: str = "r1", iter_id: str = "r1-1") -> StartedEvent:
    return StartedEvent(
        run_uuid=uuid,
        iteration_id=iter_id,
        project_name="proj",
        project_id="pid",
        iteration_count=1,
        max_iterations=3,
    )


def _verdict(uuid: str = "r1", iter_id: str = "r1-1", v: str = "pass") -> VerdictEvent:
    return VerdictEvent(
        run_uuid=uuid,
        iteration_id=iter_id,
        verdict=v,
        summary=f"summary for {v}",
        issues=IssueSummary(),
        cost_usd=0.0,
        duration_sec=1.0,
        verdict_confidence=0.9,
        issues_completeness=0.9,
    )


def _append_raw(path: Path, line: str) -> None:
    """Append a raw text line — used for torn/malformed cases tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()


# ---------------------------------------------------------------------------
# Tail-from-end default
# ---------------------------------------------------------------------------


def test_watch_starts_at_end_by_default(tmp_path: Path) -> None:
    """If audit.jsonl already has events, default behaviour is to skip
    them and only render NEW events appended after the watcher starts.
    """
    audit_path = tmp_path / "audit.jsonl"
    log = AuditLog(audit_path)
    # Pre-existing history.
    log.append(_started(iter_id="old-1"))
    log.append(_verdict(iter_id="old-1", v="pass"))

    sink = SilentRenderer()

    # New event we'll add after the watcher started.
    new_event = _started(uuid="r2", iter_id="r2-1")

    def writer() -> None:
        time.sleep(0.05)
        AuditLog(audit_path).append(new_event)

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=1,
        max_iterations=200,
    )

    # The "old-*" events must NOT be rendered — only the post-start one.
    iter_ids = [e.iteration_id for e in sink.events]
    assert "old-1" not in iter_ids
    assert "r2-1" in iter_ids


def test_watch_from_start_renders_history(tmp_path: Path) -> None:
    """``from_start=True`` renders existing history before tailing."""
    audit_path = tmp_path / "audit.jsonl"
    log = AuditLog(audit_path)
    log.append(_started(iter_id="old-1"))
    log.append(_verdict(iter_id="old-1", v="pass"))

    sink = SilentRenderer()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        from_start=True,
        stop_after_events=2,
        max_iterations=200,
    )

    iter_ids = [e.iteration_id for e in sink.events]
    assert iter_ids == ["old-1", "old-1"]


# ---------------------------------------------------------------------------
# Wait for file
# ---------------------------------------------------------------------------


def test_watch_starts_before_file_exists(tmp_path: Path) -> None:
    """Watcher started in second terminal before the first audit run —
    audit.jsonl doesn't exist yet. Watcher polls until it appears.
    """
    audit_path = tmp_path / "audit.jsonl"
    assert not audit_path.exists()

    sink = SilentRenderer()
    new_event = _started(iter_id="late-1")

    def writer() -> None:
        time.sleep(0.05)
        AuditLog(audit_path).append(new_event)

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=1,
        max_iterations=200,
    )

    assert any(e.iteration_id == "late-1" for e in sink.events)


# ---------------------------------------------------------------------------
# Append visibility
# ---------------------------------------------------------------------------


def test_watch_sees_appends_after_start(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()

    sink = SilentRenderer()
    events_to_append = [
        _started(iter_id="a-1"),
        _verdict(iter_id="a-1", v="fail"),
        _verdict(iter_id="a-2", v="pass"),
    ]

    def writer() -> None:
        time.sleep(0.05)
        log = AuditLog(audit_path)
        for e in events_to_append:
            log.append(e)
            time.sleep(0.01)

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=3,
        max_iterations=500,
    )

    iter_ids = [e.iteration_id for e in sink.events]
    assert iter_ids == ["a-1", "a-1", "a-2"]


# ---------------------------------------------------------------------------
# Delete / recreate / rotate
# ---------------------------------------------------------------------------


def test_watch_handles_file_recreated(tmp_path: Path) -> None:
    """If audit.jsonl is deleted (or rotated), watcher resets position
    and continues with the new file.
    """
    audit_path = tmp_path / "audit.jsonl"

    sink = SilentRenderer()

    def writer() -> None:
        time.sleep(0.05)
        log = AuditLog(audit_path)
        log.append(_started(iter_id="before-1"))
        time.sleep(0.05)
        # Simulate rotation: remove and recreate.
        audit_path.unlink()
        time.sleep(0.05)
        AuditLog(audit_path).append(_started(iter_id="after-1"))

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=2,
        max_iterations=1000,
    )

    iter_ids = [e.iteration_id for e in sink.events]
    assert "before-1" in iter_ids
    assert "after-1" in iter_ids


def test_watch_handles_file_truncated_smaller(tmp_path: Path) -> None:
    """If file shrinks (e.g. rewritten), watcher resets position to 0
    and re-reads from the new beginning.
    """
    audit_path = tmp_path / "audit.jsonl"
    AuditLog(audit_path).append(_started(iter_id="big-1"))

    sink = SilentRenderer()

    def writer() -> None:
        time.sleep(0.05)
        # Truncate to a smaller, different content.
        audit_path.write_text("", encoding="utf-8")
        time.sleep(0.05)
        AuditLog(audit_path).append(_started(iter_id="small-1"))

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=1,
        max_iterations=500,
    )

    iter_ids = [e.iteration_id for e in sink.events]
    assert "small-1" in iter_ids


# ---------------------------------------------------------------------------
# Tolerant to torn / malformed
# ---------------------------------------------------------------------------


def test_watch_skips_torn_last_line(tmp_path: Path) -> None:
    """A partial JSON line (no trailing \\n yet) must NOT crash the
    watcher and must NOT be rendered until it's complete.
    """
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()

    sink = SilentRenderer()

    def writer() -> None:
        time.sleep(0.05)
        # First a torn line (no newline, no closing brace).
        _append_raw(audit_path, '{"event_type":"started","run_uuid":"x"')
        time.sleep(0.05)
        # Then a valid event.
        AuditLog(audit_path).append(_started(iter_id="real-1"))

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=1,
        max_iterations=1000,
    )

    iter_ids = [e.iteration_id for e in sink.events]
    assert "real-1" in iter_ids
    # The torn line should NOT be in events (it never parsed).


def test_watch_skips_malformed_complete_line(tmp_path: Path) -> None:
    """A complete-but-malformed line (e.g. unknown event_type) is logged
    and skipped, watcher continues.
    """
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()

    sink = SilentRenderer()

    def writer() -> None:
        time.sleep(0.05)
        # Complete line with invalid JSON.
        _append_raw(audit_path, "garbage line\n")
        time.sleep(0.02)
        # Complete line with unknown event_type.
        _append_raw(
            audit_path, json.dumps({"event_type": "future_v3", "run_uuid": "x"}) + "\n"
        )
        time.sleep(0.02)
        AuditLog(audit_path).append(_started(iter_id="real-1"))

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=1,
        max_iterations=1000,
    )

    iter_ids = [e.iteration_id for e in sink.events]
    assert "real-1" in iter_ids


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------


def test_watch_does_not_modify_file(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    log = AuditLog(audit_path)
    log.append(_started(iter_id="only-1"))

    before = audit_path.read_bytes()

    sink = SilentRenderer()
    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        from_start=True,
        stop_after_events=1,
        max_iterations=100,
    )

    after = audit_path.read_bytes()
    assert before == after, "watcher must not mutate audit.jsonl"


# ---------------------------------------------------------------------------
# Bounded loop — for tests
# ---------------------------------------------------------------------------


def test_watch_returns_after_max_iterations_when_no_events(
    tmp_path: Path,
) -> None:
    """If nothing is ever appended, the loop must still terminate after
    max_iterations polling cycles. Otherwise pytest hangs forever.
    """
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()

    sink = SilentRenderer()
    started_at = time.monotonic()
    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.005,
        max_iterations=50,
    )
    elapsed = time.monotonic() - started_at

    assert sink.events == []
    # 50 iterations * 0.005s = ~0.25s; allow generous slack.
    assert elapsed < 5.0


def test_watch_stop_after_events_terminates_promptly(tmp_path: Path) -> None:
    """``stop_after_events`` short-circuits as soon as N events are
    rendered, so callers (and tests) can wait deterministically.
    """
    audit_path = tmp_path / "audit.jsonl"

    sink = SilentRenderer()

    def writer() -> None:
        time.sleep(0.05)
        log = AuditLog(audit_path)
        for i in range(5):
            log.append(_started(iter_id=f"x-{i}"))
            time.sleep(0.005)

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=2,
        max_iterations=2000,
    )

    # Stopped at exactly 2; rest were not rendered.
    assert len(sink.events) == 2


# ---------------------------------------------------------------------------
# Re-uses parse_event (smoke)
# ---------------------------------------------------------------------------


def test_watch_renders_via_parse_event_not_duplicated_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit log writer uses model_dump(mode='json'). Watcher must
    deserialize via the existing parse_event helper so any schema change
    flows through one place. We assert by checking the event class is
    the right Pydantic subclass (not a dict).
    """
    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()

    sink = SilentRenderer()

    def writer() -> None:
        time.sleep(0.05)
        AuditLog(audit_path).append(
            _verdict(iter_id="parse-1", v="needs_human")
        )

    threading.Thread(target=writer, daemon=True).start()

    watch_audit_log(
        audit_path=audit_path,
        renderer=sink,
        poll_interval_sec=0.01,
        stop_after_events=1,
        max_iterations=500,
    )

    assert len(sink.events) == 1
    rendered = sink.events[0]
    assert isinstance(rendered, VerdictEvent)
    assert isinstance(rendered, CCBridgeEvent)
    assert rendered.verdict == "needs_human"
