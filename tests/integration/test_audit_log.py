"""Integration tests for ccbridge.core.audit_log."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ccbridge.core.audit_log import AuditLog
from ccbridge.core.events import (
    IssueSummary,
    StartedEvent,
    VerdictEvent,
    WarningEvent,
)


def make_started(run_uuid: str = "run-1") -> StartedEvent:
    return StartedEvent(
        run_uuid=run_uuid,
        project_name="Test",
        project_id="proj-1",
        iteration_count=0,
        max_iterations=3,
    )


def make_verdict(
    run_uuid: str = "run-1",
    iteration_id: str = "iter-1",
    verdict: str = "pass",
) -> VerdictEvent:
    return VerdictEvent(
        run_uuid=run_uuid,
        iteration_id=iteration_id,
        verdict=verdict,  # type: ignore[arg-type]
        summary="ok",
        issues=IssueSummary(),
        cost_usd=0.05,
        duration_sec=10.0,
        verdict_confidence=0.9,
        issues_completeness=0.9,
    )


def test_append_creates_file_and_parent_dir(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / ".ccbridge" / "audit.jsonl")
    log.append(make_started())
    assert log.path.exists()


def test_append_then_read_roundtrip(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    e1 = make_started("r1")
    e2 = make_verdict("r1")
    log.append(e1)
    log.append(e2)

    events = list(log.read_all())
    assert len(events) == 2
    assert isinstance(events[0], StartedEvent)
    assert isinstance(events[1], VerdictEvent)
    assert events[0].run_uuid == "r1"
    assert events[1].verdict == "pass"


def test_each_event_is_one_line_with_newline(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    for _ in range(5):
        log.append(make_started())
    raw = log.path.read_bytes()
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 5


def test_read_all_on_missing_file_yields_nothing(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    assert list(log.read_all()) == []


def test_last_returns_none_on_empty_log(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.last() is None


def test_last_returns_most_recent_event(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started("first"))
    log.append(make_started("second"))
    log.append(make_started("third"))

    last = log.last()
    assert last is not None
    assert last.run_uuid == "third"


def test_read_tail_returns_last_n(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(10):
        log.append(make_started(f"r{i}"))

    tail = log.read_tail(3)
    assert len(tail) == 3
    assert [e.run_uuid for e in tail] == ["r7", "r8", "r9"]


def test_read_tail_n_zero_returns_empty(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started())
    assert log.read_tail(0) == []


def test_read_tail_n_larger_than_log_returns_all(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started("only"))

    tail = log.read_tail(100)
    assert len(tail) == 1


def test_tolerant_reader_skips_torn_last_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Simulate a crash mid-write that left the final line malformed."""
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started("complete"))

    # Append a torn (truncated) JSON line by hand.
    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"event_type":"verdict","run_uuid":"r","verd')

    with caplog.at_level(logging.WARNING, logger="ccbridge.core.audit_log"):
        events = list(log.read_all())

    assert len(events) == 1
    assert events[0].run_uuid == "complete"
    assert any("unparseable" in rec.message for rec in caplog.records)


def test_tolerant_reader_skips_torn_middle_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A bad line in the middle does not stop later lines from parsing."""
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started("first"))

    with log.path.open("a", encoding="utf-8") as f:
        f.write("{not json at all\n")

    log.append(make_started("third"))

    with caplog.at_level(logging.WARNING, logger="ccbridge.core.audit_log"):
        events = list(log.read_all())

    assert len(events) == 2
    assert [e.run_uuid for e in events] == ["first", "third"]


def test_unknown_event_type_is_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Forward-compat: unknown event_type from a newer schema is skipped."""
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started("known"))

    with log.path.open("a", encoding="utf-8") as f:
        f.write('{"event_type":"future_event","run_uuid":"x","ts":"2026-05-02T10:00:00+00:00"}\n')

    log.append(make_started("after"))

    with caplog.at_level(logging.WARNING, logger="ccbridge.core.audit_log"):
        events = list(log.read_all())

    assert [e.run_uuid for e in events] == ["known", "after"]


def test_blank_lines_are_silently_skipped(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(make_started("first"))

    with log.path.open("a", encoding="utf-8") as f:
        f.write("\n\n")

    log.append(make_started("second"))

    events = list(log.read_all())
    assert len(events) == 2


def test_size_and_line_count_helpers(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.size_bytes() == 0
    assert log.line_count() == 0

    log.append(make_started())
    log.append(make_verdict())

    assert log.size_bytes() > 0
    assert log.line_count() == 2


def test_unicode_round_trip(tmp_path: Path) -> None:
    """Cyrillic content must survive serialization (no ASCII escape)."""
    log = AuditLog(tmp_path / "audit.jsonl")
    event = WarningEvent(
        run_uuid="run-кириллица",
        message="дропнул issue: файл не в diff",
        context={"файл": "src/foo.py"},
    )
    log.append(event)

    raw = log.path.read_text(encoding="utf-8")
    # We store as real UTF-8, not as \u-escapes, so the cyrillic appears verbatim.
    assert "дропнул issue" in raw

    events = list(log.read_all())
    assert len(events) == 1
    assert isinstance(events[0], WarningEvent)
    assert events[0].context["файл"] == "src/foo.py"


def test_append_preserves_event_order_across_reopens(tmp_path: Path) -> None:
    """A new AuditLog instance reading the same path sees prior writes."""
    path = tmp_path / "audit.jsonl"
    AuditLog(path).append(make_started("first"))
    AuditLog(path).append(make_started("second"))
    AuditLog(path).append(make_started("third"))

    fresh_reader = AuditLog(path)
    events = list(fresh_reader.read_all())
    assert [e.run_uuid for e in events] == ["first", "second", "third"]
