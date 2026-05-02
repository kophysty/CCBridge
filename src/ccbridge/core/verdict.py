"""Verdict schema and semantic validation.

Pydantic enforces *types*; this module also enforces *semantics* —
the things an LLM reviewer might violate even when it returns valid JSON:

* `verdict=pass` while listing critical/major issues (LLM sycophancy)
* citing files that don't exist in the diff (hallucination)
* citing line numbers past the end of file
* citing rule_ids that weren't provided

See ARCHITECTURE.md §2.5 and §2.5.1 for design rationale.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


Severity = Literal["critical", "major", "minor", "info"]
Category = Literal[
    "security",
    "correctness",
    "performance",
    "style",
    "maintainability",
    "testing",
    "rule-violation",
]
VerdictLabel = Literal["pass", "fail", "needs_human"]

CONFIDENCE_THRESHOLD_DEFAULT = 0.7


class Issue(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    severity: Severity
    category: Category
    file: str
    line: int | None = None
    message: str
    rule_id: str | None = None
    suggested_fix: str | None = Field(default=None, max_length=2000)


class Verdict(BaseModel):
    """Structured review outcome from Codex.

    A successful Pydantic parse only guarantees shape. Use
    `validate_semantics()` afterwards to enforce file/line/rule
    consistency against the actual diff.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal[1] = 1
    verdict: VerdictLabel
    summary: str = Field(..., max_length=500)
    issues: list[Issue] = Field(default_factory=list)
    verdict_confidence: float = Field(..., ge=0.0, le=1.0)
    issues_completeness: float = Field(..., ge=0.0, le=1.0)
    files_reviewed: list[str] = Field(default_factory=list)
    rules_checked: list[str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def severity_implies_failure(self) -> Verdict:
        """LLM sycophancy guard.

        If any issue is critical or major, verdict cannot be `pass`.
        We allow `needs_human` (the reviewer is uncertain) and `fail`
        (the reviewer wants Claude to fix).
        """
        severities = {i.severity for i in self.issues}
        if {"critical", "major"} & severities and self.verdict == "pass":
            raise ValueError(
                f"verdict=pass illegal with severities {sorted(severities)}; "
                f"must be 'fail' or 'needs_human'"
            )
        return self


# ---------------------------------------------------------------------------
# Semantic validation (post-Pydantic)
# ---------------------------------------------------------------------------


class ValidationWarning(BaseModel):
    """A single semantic-validation warning."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    reason: str  # e.g. "file_not_in_diff", "line_out_of_bounds", "unknown_rule_id"
    issue_index: int  # position in the original Verdict.issues list
    detail: str


class ValidatedVerdict(BaseModel):
    """Result of semantic validation: a (possibly trimmed) verdict + warnings.

    The verdict label may differ from the input — e.g. effective downgrade
    to `needs_human` when verdict_confidence is below threshold, or when
    all issues were dropped but verdict was `fail`.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    verdict: Verdict
    warnings: list[ValidationWarning] = Field(default_factory=list)
    effective_verdict: VerdictLabel
    effective_completeness: float


def validate_semantics(
    verdict: Verdict,
    *,
    diff_files: set[str],
    file_line_counts: dict[str, int],
    known_rule_ids: set[str] | None = None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD_DEFAULT,
) -> ValidatedVerdict:
    """Drop issues that don't survive semantic checks; compute effective verdict.

    Parameters
    ----------
    verdict
        The Pydantic-parsed verdict from Codex.
    diff_files
        Set of files (forward-slash paths) actually present in the current
        diff snapshot. Issues citing other files are dropped.
    file_line_counts
        Map from file path to its line count. Issues with line > count
        are dropped.
    known_rule_ids
        If provided, issues citing rule_ids outside this set are dropped.
        Pass ``None`` to skip the check (e.g. when the project has no
        rulebook).
    confidence_threshold
        If `verdict_confidence` is below this and the verdict was `pass`,
        the effective verdict is downgraded to `needs_human`.

    Returns
    -------
    ValidatedVerdict
        - `verdict`: a new Verdict with surviving issues
        - `warnings`: structured list of every drop with the reason
        - `effective_verdict`: possibly downgraded
        - `effective_completeness`: lowered if issues were dropped
    """
    warnings: list[ValidationWarning] = []
    surviving: list[Issue] = []

    for index, issue in enumerate(verdict.issues):
        if issue.file not in diff_files:
            warnings.append(
                ValidationWarning(
                    reason="file_not_in_diff",
                    issue_index=index,
                    detail=f"file={issue.file} not among diff_files",
                )
            )
            continue

        if issue.line is not None:
            max_line = file_line_counts.get(issue.file)
            if max_line is not None and issue.line > max_line:
                warnings.append(
                    ValidationWarning(
                        reason="line_out_of_bounds",
                        issue_index=index,
                        detail=f"file={issue.file} line={issue.line} > {max_line}",
                    )
                )
                continue

        if (
            known_rule_ids is not None
            and issue.rule_id is not None
            and issue.rule_id not in known_rule_ids
        ):
            warnings.append(
                ValidationWarning(
                    reason="unknown_rule_id",
                    issue_index=index,
                    detail=f"rule_id={issue.rule_id} not in provided rules",
                )
            )
            continue

        surviving.append(issue)

    # Build the trimmed verdict.
    dropped = len(verdict.issues) - len(surviving)
    completeness = verdict.issues_completeness
    if dropped > 0:
        # Penalise completeness proportionally to how many were dropped.
        completeness = max(0.0, completeness * (1 - 0.1 * dropped))

    # Determine effective verdict.
    effective: VerdictLabel = verdict.verdict
    surviving_severities = {i.severity for i in surviving}

    # If the input was `fail` but every issue got dropped, we lose justification
    # for `fail`. Codex saw problems but described them invalidly — escalate.
    if verdict.verdict == "fail" and not surviving:
        effective = "needs_human"

    # If the input was `pass` but confidence is low, escalate.
    if verdict.verdict == "pass" and verdict.verdict_confidence < confidence_threshold:
        effective = "needs_human"

    # Construct the trimmed Verdict. The model_validator must be re-satisfied.
    # If dropping issues now makes `pass` legal where it wasn't before, that's
    # the intended semantics — semantic validation already filtered out invalid
    # issues, so the remaining set represents Codex's accurate findings.
    trimmed = Verdict(
        schema_version=verdict.schema_version,
        verdict=verdict.verdict,
        summary=verdict.summary,
        issues=surviving,
        verdict_confidence=verdict.verdict_confidence,
        issues_completeness=completeness,
        files_reviewed=verdict.files_reviewed,
        rules_checked=verdict.rules_checked,
    )

    if dropped > 0:
        logger.warning(
            "verdict semantic validation dropped %d/%d issues",
            dropped,
            len(verdict.issues),
        )

    # Sanity: even after trimming, surviving severities must still permit `pass`.
    # The model_validator on Verdict already handles this for the trimmed model,
    # but we double-check effective:
    if effective == "pass" and {"critical", "major"} & surviving_severities:
        # Should be unreachable because the trimmed Verdict construction would
        # have raised. Defensive log just in case the schema evolves.
        logger.error(
            "semantic validation produced inconsistent state: "
            "effective=pass with severities=%s",
            sorted(surviving_severities),
        )
        effective = "needs_human"

    return ValidatedVerdict(
        verdict=trimmed,
        warnings=warnings,
        effective_verdict=effective,
        effective_completeness=completeness,
    )
