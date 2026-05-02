"""Tests for ccbridge.core.verdict."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ccbridge.core.verdict import (
    Issue,
    ValidatedVerdict,
    Verdict,
    validate_semantics,
)


def make_issue(
    severity: str = "minor",
    file: str = "src/foo.py",
    line: int | None = 10,
    rule_id: str | None = None,
) -> Issue:
    return Issue(
        severity=severity,  # type: ignore[arg-type]
        category="correctness",
        file=file,
        line=line,
        message="test issue",
        rule_id=rule_id,
    )


def make_verdict(
    verdict: str = "pass",
    issues: list[Issue] | None = None,
    confidence: float = 0.9,
    completeness: float = 0.9,
) -> Verdict:
    return Verdict(
        verdict=verdict,  # type: ignore[arg-type]
        summary="test",
        issues=issues or [],
        verdict_confidence=confidence,
        issues_completeness=completeness,
        files_reviewed=["src/foo.py"],
        rules_checked=["R-001"],
    )


# ---------------------------------------------------------------------------
# Pydantic-level validation
# ---------------------------------------------------------------------------


def test_verdict_pass_with_no_issues_ok() -> None:
    v = make_verdict(verdict="pass")
    assert v.verdict == "pass"


def test_verdict_pass_with_critical_issue_rejected() -> None:
    with pytest.raises(ValidationError, match="verdict=pass illegal"):
        make_verdict(verdict="pass", issues=[make_issue(severity="critical")])


def test_verdict_pass_with_major_issue_rejected() -> None:
    with pytest.raises(ValidationError, match="verdict=pass illegal"):
        make_verdict(verdict="pass", issues=[make_issue(severity="major")])


def test_verdict_pass_with_minor_issues_allowed() -> None:
    v = make_verdict(verdict="pass", issues=[make_issue(severity="minor")])
    assert v.verdict == "pass"
    assert len(v.issues) == 1


def test_verdict_fail_with_critical_allowed() -> None:
    v = make_verdict(verdict="fail", issues=[make_issue(severity="critical")])
    assert v.verdict == "fail"


def test_verdict_needs_human_with_critical_allowed() -> None:
    v = make_verdict(
        verdict="needs_human", issues=[make_issue(severity="critical")]
    )
    assert v.verdict == "needs_human"


def test_verdict_rules_checked_min_length_one() -> None:
    with pytest.raises(ValidationError):
        Verdict(
            verdict="pass",
            summary="x",
            issues=[],
            verdict_confidence=0.9,
            issues_completeness=0.9,
            files_reviewed=[],
            rules_checked=[],  # empty — must fail
        )


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        make_verdict(confidence=1.5)
    with pytest.raises(ValidationError):
        make_verdict(confidence=-0.1)


def test_summary_max_length_enforced() -> None:
    with pytest.raises(ValidationError):
        Verdict(
            verdict="pass",
            summary="x" * 501,
            verdict_confidence=0.9,
            issues_completeness=0.9,
            rules_checked=["R-001"],
        )


# ---------------------------------------------------------------------------
# Semantic validation: file/line/rule_id checks
# ---------------------------------------------------------------------------


def test_semantic_drops_issue_when_file_not_in_diff() -> None:
    verdict = make_verdict(
        verdict="fail",
        issues=[make_issue(severity="major", file="src/not_changed.py")],
    )
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
    )
    assert len(result.verdict.issues) == 0
    assert len(result.warnings) == 1
    assert result.warnings[0].reason == "file_not_in_diff"


def test_semantic_drops_issue_when_line_out_of_bounds() -> None:
    verdict = make_verdict(
        verdict="fail",
        issues=[make_issue(severity="major", file="src/foo.py", line=9999)],
    )
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 50},
    )
    assert len(result.verdict.issues) == 0
    assert result.warnings[0].reason == "line_out_of_bounds"


def test_semantic_drops_issue_when_rule_id_unknown() -> None:
    verdict = make_verdict(
        verdict="fail",
        issues=[make_issue(severity="major", rule_id="R-099")],
    )
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
        known_rule_ids={"R-001", "R-002"},
    )
    assert len(result.verdict.issues) == 0
    assert result.warnings[0].reason == "unknown_rule_id"


def test_semantic_skips_rule_id_check_when_known_ids_none() -> None:
    """known_rule_ids=None means project has no rulebook — accept any rule_id."""
    verdict = make_verdict(
        verdict="fail",
        issues=[make_issue(severity="major", rule_id="R-anything")],
    )
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
        known_rule_ids=None,
    )
    assert len(result.verdict.issues) == 1


def test_semantic_keeps_valid_issues() -> None:
    issues = [
        make_issue(severity="major", file="src/foo.py", line=10, rule_id="R-001"),
        make_issue(severity="minor", file="src/foo.py", line=20, rule_id="R-002"),
    ]
    verdict = make_verdict(verdict="fail", issues=issues)
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
        known_rule_ids={"R-001", "R-002"},
    )
    assert len(result.verdict.issues) == 2
    assert len(result.warnings) == 0


def test_semantic_lowers_completeness_when_dropping() -> None:
    verdict = make_verdict(
        verdict="fail",
        issues=[
            make_issue(severity="major", file="missing.py"),
            make_issue(severity="major", file="missing2.py"),
        ],
        completeness=1.0,
    )
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
    )
    assert result.effective_completeness < 1.0
    assert result.effective_completeness == result.verdict.issues_completeness


# ---------------------------------------------------------------------------
# Effective verdict logic
# ---------------------------------------------------------------------------


def test_effective_pass_downgrades_when_confidence_below_threshold() -> None:
    verdict = make_verdict(verdict="pass", confidence=0.5)
    result = validate_semantics(
        verdict,
        diff_files=set(),
        file_line_counts={},
    )
    assert result.effective_verdict == "needs_human"


def test_effective_pass_kept_when_confidence_ok() -> None:
    verdict = make_verdict(verdict="pass", confidence=0.9)
    result = validate_semantics(
        verdict,
        diff_files=set(),
        file_line_counts={},
    )
    assert result.effective_verdict == "pass"


def test_effective_fail_escalated_when_all_issues_dropped() -> None:
    """If Codex said 'fail' but every issue was hallucinated, we don't know
    if there are real issues or not — escalate to needs_human."""
    verdict = make_verdict(
        verdict="fail",
        issues=[make_issue(severity="major", file="hallucinated.py")],
    )
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
    )
    assert result.effective_verdict == "needs_human"
    assert len(result.verdict.issues) == 0


def test_effective_fail_kept_when_some_issues_survive() -> None:
    issues = [
        make_issue(severity="major", file="hallucinated.py"),
        make_issue(severity="major", file="src/foo.py", line=10),
    ]
    verdict = make_verdict(verdict="fail", issues=issues)
    result = validate_semantics(
        verdict,
        diff_files={"src/foo.py"},
        file_line_counts={"src/foo.py": 100},
    )
    assert result.effective_verdict == "fail"
    assert len(result.verdict.issues) == 1


def test_validated_verdict_is_typed() -> None:
    verdict = make_verdict()
    result = validate_semantics(
        verdict, diff_files=set(), file_line_counts={}
    )
    assert isinstance(result, ValidatedVerdict)
    assert isinstance(result.verdict, Verdict)


def test_custom_confidence_threshold() -> None:
    verdict = make_verdict(verdict="pass", confidence=0.6)
    # threshold 0.5 — should pass
    result = validate_semantics(
        verdict,
        diff_files=set(),
        file_line_counts={},
        confidence_threshold=0.5,
    )
    assert result.effective_verdict == "pass"
    # threshold 0.7 (default) — should escalate
    result = validate_semantics(
        verdict,
        diff_files=set(),
        file_line_counts={},
        confidence_threshold=0.7,
    )
    assert result.effective_verdict == "needs_human"
