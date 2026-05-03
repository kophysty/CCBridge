"""Integration tests for claude_runner.

We don't shell out to a real `claude` binary. Instead we monkeypatch
`subprocess.run` with a stub that mimics the documented contract:

    claude --print --output-format json <prompt> → JSON on stdout, exit 0

The runner's job is to: build the right argv, pass the prompt on stdin
(if applicable), parse stdout as JSON, and surface non-zero exits as a
structured error (not a raw CalledProcessError).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ccbridge.runners.claude_runner import (
    ClaudeRunnerError,
    ClaudeRunResult,
    run_claude,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[list[str], dict[str, Any]], subprocess.CompletedProcess[str]],
) -> list[tuple[list[str], dict[str, Any]]]:
    """Replace subprocess.run with a stub. Return a captured-calls list."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return handler(argv, kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _resolve_executable_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default: resolve_executable is identity for bare names.

    Real shutil.which is not available in CI test envs (no claude
    installed). Tests that exercise the resolver explicitly override
    via their own monkeypatch.setattr.
    """
    monkeypatch.setattr(
        "ccbridge.runners.claude_runner.resolve_executable",
        lambda name: name,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_claude_returns_parsed_json_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {"role": "assistant", "content": "all good"}

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    _stub_run(monkeypatch, handler)

    result = run_claude(prompt="hello", cwd=tmp_path)

    assert isinstance(result, ClaudeRunResult)
    assert result.parsed == payload
    assert result.returncode == 0
    assert result.stdout == json.dumps(payload)


def test_run_claude_argv_skeleton_and_no_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """argv skeleton: claude --print --output-format json. Prompt MUST
    NOT appear in argv (audit finding #2 — Windows cmdline size limit
    + OWASP A03 defense-in-depth). Prompt arrives via stdin instead.
    """

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"k":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    secret_marker = "DISTINCTIVE_PROMPT_MARKER_J42"
    run_claude(prompt=secret_marker, cwd=tmp_path)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[0] == "claude"
    assert "--print" in argv
    assert "--output-format" in argv
    fmt_idx = argv.index("--output-format")
    assert argv[fmt_idx + 1] == "json"

    assert secret_marker not in " ".join(argv), (
        f"prompt leaked into argv: {argv}"
    )
    assert kwargs.get("input") == secret_marker
    assert kwargs.get("cwd") == tmp_path


def test_run_claude_inherits_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """API key must reach the subprocess via env. We pass env=None
    (inherit) by default; the caller can override.
    """

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"x":0}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_claude(prompt="x", cwd=tmp_path)

    _, kwargs = calls[0]
    # When no env override is given we must NOT pass env={} (which would
    # strip the API key); inherit by passing env=None or omitting it.
    assert kwargs.get("env") is None or "ANTHROPIC_API_KEY" in (kwargs.get("env") or {})


def test_run_claude_accepts_explicit_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Caller can pass an explicit env dict (e.g. for tests)."""

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"x":0}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    custom_env = {"ANTHROPIC_API_KEY": "sk-test", "PATH": "/usr/bin"}
    run_claude(prompt="x", cwd=tmp_path, env=custom_env)

    _, kwargs = calls[0]
    assert kwargs.get("env") == custom_env


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_run_claude_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=2, stdout="", stderr="boom"
        )

    _stub_run(monkeypatch, handler)

    with pytest.raises(ClaudeRunnerError) as exc_info:
        run_claude(prompt="x", cwd=tmp_path)

    err = exc_info.value
    assert err.returncode == 2
    assert "boom" in err.stderr


def test_run_claude_raises_on_invalid_json_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=0, stdout="not-json", stderr=""
        )

    _stub_run(monkeypatch, handler)

    with pytest.raises(ClaudeRunnerError) as exc_info:
        run_claude(prompt="x", cwd=tmp_path)

    assert "json" in str(exc_info.value).lower()
    assert exc_info.value.returncode == 0
    assert exc_info.value.stdout == "not-json"


def test_run_claude_raises_on_executable_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If ``claude`` is not in PATH at all, resolve_executable raises and
    we surface it as ClaudeRunnerError before subprocess is invoked.
    """
    monkeypatch.setattr(
        "ccbridge.runners.claude_runner.resolve_executable",
        lambda name: (_ for _ in ()).throw(
            FileNotFoundError(f"executable {name!r} not in PATH")
        ),
    )

    with pytest.raises(ClaudeRunnerError) as exc_info:
        run_claude(prompt="x", cwd=tmp_path)

    msg = str(exc_info.value).lower()
    assert "not found" in msg or "not in path" in msg or "claude" in msg


def test_run_claude_uses_resolved_executable_in_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Windows: ``claude`` is typically installed as
    ``claude.cmd`` via npm. resolve_executable handles PATHEXT —
    argv[0] must be the resolved full path.
    """
    fake_resolved = "/fake/path/to/claude.cmd"
    monkeypatch.setattr(
        "ccbridge.runners.claude_runner.resolve_executable",
        lambda name: fake_resolved if name == "claude" else name,
    )

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"x":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_claude(prompt="x", cwd=tmp_path)

    argv, _ = calls[0]
    assert argv[0] == fake_resolved, (
        f"argv[0] should be the resolved path, got: {argv[0]!r}"
    )


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_run_claude_propagates_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    _stub_run(monkeypatch, handler)

    with pytest.raises(ClaudeRunnerError) as exc_info:
        run_claude(prompt="x", cwd=tmp_path, timeout=5)

    msg = str(exc_info.value).lower()
    assert "timed out" in msg or "timeout" in msg
    assert isinstance(exc_info.value.cause, subprocess.TimeoutExpired)


def test_run_claude_passes_timeout_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"x":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_claude(prompt="x", cwd=tmp_path, timeout=42)

    _, kwargs = calls[0]
    assert kwargs.get("timeout") == 42


def test_run_claude_uses_utf8_encoding_for_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: same Windows cp1251 issue as codex_runner.
    subprocess.run(text=True) defaults to locale encoding; we must
    force utf-8 so prompts containing arrows / cyrillic / emoji
    don't crash on Russian Windows.
    """

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"x":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    prompt_with_unicode = "rules → check\nкириллица тест"  # noqa: RUF001
    run_claude(prompt=prompt_with_unicode, cwd=tmp_path)

    _, kwargs = calls[0]
    assert kwargs.get("encoding") == "utf-8"


# ---------------------------------------------------------------------------
# Custom executable path
# ---------------------------------------------------------------------------


def test_run_claude_accepts_custom_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the user has claude under a non-standard path, allow override."""

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"x":0}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_claude(prompt="x", cwd=tmp_path, executable="/opt/claude/bin/claude")

    argv, _ = calls[0]
    assert argv[0] == "/opt/claude/bin/claude"
