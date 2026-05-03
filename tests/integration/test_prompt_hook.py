"""Integration tests for transports/prompt_hook.

UserPromptSubmit hook is invoked by Claude Code when the user submits a
prompt. JSON on stdin per the docs:

    {
        "session_id": "<uuid>",
        "transcript_path": "<path>",
        "cwd": "<path>",
        "permission_mode": "<mode>",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "<text>"
    }

Source: https://code.claude.com/docs/en/hooks (UserPromptSubmit event).

Contract (audit-confirmed 2026-05-03):

* If ``payload.prompt`` (case-folded) contains ``[review] skip_marker``
  (default ``[skip-review]``) — write ``.ccbridge/skip-review.json``
  with session_id + transcript_path metadata + ISO8601 ``created_at``
  + ``reason="user_marker"``.
* No marker → no marker file written.
* Missing/non-str ``prompt`` → fail-open, no file, empty stdout.
* Missing ``session_id`` → fail-open, no file, empty stdout.
* Any internal error (write failure, malformed JSON) → fail-open:
  empty stdout, stderr diagnostic, exit 0.
* stdout MUST stay empty (we are not making a decision; we just record
  a hint for Stop hook).

Marker is searched **only** in ``payload.prompt`` (security boundary —
Claude can self-write transcript content but not the user's typed
prompt). Search is substring match after ``.casefold()``, no regex.

Project resolution mirrors stop_hook: CLAUDE_PROJECT_DIR primary,
``cwd`` fallback. Marker file goes to ``<project>/.ccbridge/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ccbridge.transports.prompt_hook import prompt_hook_main


@dataclass(frozen=True)
class HookResult:
    exit_code: int
    stdout: str
    stderr: str


def _run_hook(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    stdin: dict[str, Any] | str,
    project_dir: Path | None = None,
) -> HookResult:
    """Invoke prompt_hook_main with given stdin and CLAUDE_PROJECT_DIR."""
    import sys
    from io import StringIO

    if project_dir is not None:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

    text = stdin if isinstance(stdin, str) else json.dumps(stdin)
    monkeypatch.setattr(sys, "stdin", StringIO(text))

    exit_code = prompt_hook_main()
    captured = capsys.readouterr()
    return HookResult(
        exit_code=exit_code, stdout=captured.out, stderr=captured.err
    )


def _payload(
    *,
    prompt: Any = "hello",
    session_id: str | None = "sess-abc",
    transcript_path: str = "/tmp/transcript.jsonl",
    cwd: str = "/tmp/proj",
    permission_mode: str = "default",
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "transcript_path": transcript_path,
        "cwd": cwd,
        "permission_mode": permission_mode,
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
    }
    if session_id is not None:
        p["session_id"] = session_id
    return p


# ---------------------------------------------------------------------------
# Happy path: marker present → file written
# ---------------------------------------------------------------------------


def test_marker_present_writes_skip_review_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="just a quick fix [skip-review]"),
        project_dir=repo,
    )

    assert result.exit_code == 0
    assert result.stdout == ""

    marker_path = repo / ".ccbridge" / "skip-review.json"
    assert marker_path.exists()

    data = json.loads(marker_path.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-abc"
    assert data["transcript_path"] == "/tmp/transcript.jsonl"
    assert data["reason"] == "user_marker"
    assert data["marker"] == "[skip-review]"
    # created_at must be valid ISO8601 UTC.
    datetime.fromisoformat(data["created_at"])


def test_marker_case_insensitive_via_casefold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """[Skip-Review], [SKIP-REVIEW], [skip-review] all match (casefold)."""
    repo = tmp_path
    for variant in ("[Skip-Review]", "[SKIP-REVIEW]", "[skip-review]"):
        marker_path = repo / ".ccbridge" / "skip-review.json"
        if marker_path.exists():
            marker_path.unlink()

        result = _run_hook(
            monkeypatch,
            capsys,
            stdin=_payload(prompt=f"trivial change {variant} thanks"),
            project_dir=repo,
        )
        assert result.exit_code == 0, f"variant {variant} failed"
        assert marker_path.exists(), f"variant {variant} did not write marker"


def test_marker_inline_anywhere_in_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Marker may appear at the start, middle, or end of prompt."""
    repo = tmp_path
    for prompt in (
        "[skip-review] do this small thing",
        "do this small thing [skip-review] please",
        "rename foo to bar [skip-review]",
    ):
        marker_path = repo / ".ccbridge" / "skip-review.json"
        if marker_path.exists():
            marker_path.unlink()

        result = _run_hook(
            monkeypatch,
            capsys,
            stdin=_payload(prompt=prompt),
            project_dir=repo,
        )
        assert result.exit_code == 0
        assert marker_path.exists(), f"marker not written for prompt: {prompt!r}"


# ---------------------------------------------------------------------------
# No-marker: file not written
# ---------------------------------------------------------------------------


def test_no_marker_does_not_write_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="please review this carefully"),
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert not (repo / ".ccbridge" / "skip-review.json").exists()


# ---------------------------------------------------------------------------
# Fail-open guardrails (audit requirement)
# ---------------------------------------------------------------------------


def test_missing_session_id_fails_open_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Marker requires session_id (Stop hook matches by it)."""
    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="do it [skip-review]", session_id=None),
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert not (repo / ".ccbridge" / "skip-review.json").exists()
    assert result.stderr  # diagnostic on stderr


def test_non_str_prompt_fails_open_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    # prompt is a list — invalid per schema but we must not crash.
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt=["skip-review"]),  # type: ignore[arg-type]
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert not (repo / ".ccbridge" / "skip-review.json").exists()


def test_invalid_json_stdin_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin="not valid json {",
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert not (repo / ".ccbridge" / "skip-review.json").exists()
    assert result.stderr


def test_empty_stdin_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin="",
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert not (repo / ".ccbridge" / "skip-review.json").exists()


def test_missing_project_dir_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both env and cwd missing → can't locate .ccbridge — fail-open."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin={
            "session_id": "s",
            "prompt": "do this [skip-review]",
            "cwd": "/nonexistent/path/xyz",
            "transcript_path": "/tmp/t.jsonl",
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
        },
    )
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


def test_project_dir_from_env_wins_over_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="x [skip-review] y", cwd=str(other)),
        project_dir=real,
    )
    assert result.exit_code == 0
    assert (real / ".ccbridge" / "skip-review.json").exists()
    assert not (other / ".ccbridge" / "skip-review.json").exists()


def test_project_dir_falls_back_to_cwd_when_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="x [skip-review] y", cwd=str(repo)),
    )
    assert result.exit_code == 0
    assert (repo / ".ccbridge" / "skip-review.json").exists()


# ---------------------------------------------------------------------------
# Created_at is ISO8601 UTC and recent
# ---------------------------------------------------------------------------


def test_created_at_is_iso8601_utc_recent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="[skip-review]"),
        project_dir=repo,
    )
    assert result.exit_code == 0

    data = json.loads(
        (repo / ".ccbridge" / "skip-review.json").read_text(encoding="utf-8")
    )
    created = datetime.fromisoformat(data["created_at"])
    # Must be timezone-aware UTC (not naive).
    assert created.tzinfo is not None
    age = datetime.now(UTC) - created
    assert age.total_seconds() < 5  # written just now


# ---------------------------------------------------------------------------
# Config-driven skip_marker (Blocker #1)
# ---------------------------------------------------------------------------
# prompt_hook must read [review] skip_marker from .ccbridge/config.toml
# and use that custom marker. Default `[skip-review]` is ONLY a fallback
# when no config (or no `skip_marker` field) is present.


def _write_project_config(repo: Path, body: str) -> None:
    cfg_dir = repo / ".ccbridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(body, encoding="utf-8")


def test_custom_skip_marker_from_config_writes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If config.toml sets skip_marker = "[no-audit]", that's the marker
    that gets matched (NOT the default `[skip-review]`).
    """
    repo = tmp_path
    _write_project_config(
        repo, '[review]\nskip_marker = "[no-audit]"\n'
    )

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="quick fix [no-audit]"),
        project_dir=repo,
    )
    assert result.exit_code == 0
    marker_path = repo / ".ccbridge" / "skip-review.json"
    assert marker_path.exists()
    data = json.loads(marker_path.read_text(encoding="utf-8"))
    assert data["marker"] == "[no-audit]"


def test_default_skip_marker_does_not_match_when_custom_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If config sets a custom marker, the default `[skip-review]` must
    NOT trigger marker write. Otherwise the user's expectation
    (`[no-audit]` is the only opt-out) is broken.
    """
    repo = tmp_path
    _write_project_config(
        repo, '[review]\nskip_marker = "[no-audit]"\n'
    )

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="please [skip-review] this"),
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert not (repo / ".ccbridge" / "skip-review.json").exists()


def test_default_marker_used_when_config_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No config.toml → fall back to default `[skip-review]`."""
    repo = tmp_path
    # No config written.
    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="trivial [skip-review]"),
        project_dir=repo,
    )
    assert result.exit_code == 0
    assert (repo / ".ccbridge" / "skip-review.json").exists()


def test_malformed_config_falls_back_to_default_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """config.toml with invalid TOML → fail-open (use default marker,
    write stderr diagnostic). prompt_hook must NEVER crash Claude.
    """
    repo = tmp_path
    _write_project_config(repo, "this is not = valid [[[ toml")

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="trivial [skip-review]"),
        project_dir=repo,
    )
    assert result.exit_code == 0
    # Default marker still applies on broken config.
    assert (repo / ".ccbridge" / "skip-review.json").exists()


# ---------------------------------------------------------------------------
# hook_event_name validation (Blocker #2 part)
# ---------------------------------------------------------------------------
# The hook MUST verify that the input claims to be UserPromptSubmit.
# Any other event name → fail-open, no marker file. This blocks a class
# of accidental misroutes (e.g. someone wires prompt-hook command to
# the Stop event by mistake).


def test_wrong_hook_event_name_does_not_write_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    payload = _payload(prompt="[skip-review]")
    payload["hook_event_name"] = "Stop"  # wrong event

    result = _run_hook(monkeypatch, capsys, stdin=payload, project_dir=repo)
    assert result.exit_code == 0
    assert not (repo / ".ccbridge" / "skip-review.json").exists()


def test_missing_hook_event_name_does_not_write_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path
    payload = _payload(prompt="[skip-review]")
    del payload["hook_event_name"]

    result = _run_hook(monkeypatch, capsys, stdin=payload, project_dir=repo)
    assert result.exit_code == 0
    assert not (repo / ".ccbridge" / "skip-review.json").exists()


# ---------------------------------------------------------------------------
# HMAC-signed marker (Blocker #2)
# ---------------------------------------------------------------------------
# A malicious process with write-access to .ccbridge/ could otherwise
# forge a marker file with the right session_id and bypass audit.
# Defense: prompt_hook signs the marker with HMAC-SHA256 using a secret
# stored in the user's HOME (~/.ccbridge/skip-review.secret), NOT in
# the project workspace. Stop hook re-computes the HMAC and rejects
# any marker that doesn't validate.
#
# Threat model boundary: the user-home secret protects against
# workspace-write attacks (e.g. compromised dependency in the project,
# CI runner with write-access to repo). It does NOT protect against
# full user-account compromise — that's outside scope for v0.1.


def test_marker_file_contains_hmac_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The marker file must include a `signature` field that the Stop
    hook will validate.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows
    repo = tmp_path / "proj"
    repo.mkdir()

    result = _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="[skip-review]"),
        project_dir=repo,
    )
    assert result.exit_code == 0
    data = json.loads(
        (repo / ".ccbridge" / "skip-review.json").read_text(encoding="utf-8")
    )
    assert "signature" in data
    assert isinstance(data["signature"], str)
    # HMAC-SHA256 hex = 64 chars.
    assert len(data["signature"]) == 64


def test_secret_file_created_in_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """First marker write must create ~/.ccbridge/skip-review.secret if
    it doesn't already exist.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = tmp_path / "proj"
    repo.mkdir()

    _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="[skip-review]"),
        project_dir=repo,
    )

    from ccbridge.transports.prompt_hook import _user_secret_path

    secret_path = _user_secret_path()
    assert secret_path.exists(), f"secret file not created at {secret_path}"
    # 32 bytes (256-bit) — store as hex (64 chars).
    content = secret_path.read_text(encoding="utf-8").strip()
    assert len(content) == 64


def test_secret_file_reused_on_second_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Don't regenerate the secret on every prompt — the first stored
    value persists.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = tmp_path / "proj"
    repo.mkdir()

    _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="[skip-review]"),
        project_dir=repo,
    )

    from ccbridge.transports.prompt_hook import _user_secret_path

    secret_path = _user_secret_path()
    secret_first = secret_path.read_text(encoding="utf-8")

    # Second invocation
    _run_hook(
        monkeypatch,
        capsys,
        stdin=_payload(prompt="[skip-review]", session_id="sess-2"),
        project_dir=repo,
    )

    secret_second = secret_path.read_text(encoding="utf-8")
    assert secret_first == secret_second
