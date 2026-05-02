"""Integration tests for codex_runner.

Codex is invoked non-interactively::

    codex exec --json <prompt>

Unlike claude, Codex sometimes wraps its JSON output in markdown fences
(```json ... ```), which we extract before parsing. Codex is also subject
to OpenAI rate limits (HTTP 429), so the runner retries with backoff
honouring ``Retry-After`` when present.

We never invoke a real codex binary in tests; subprocess.run is
monkeypatched.

ARCHITECTURE.md §6.1 — API key reaches the subprocess via inherited env
under the name configured in [codex] api_key_env (default OPENAI_API_KEY).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ccbridge.runners.codex_runner import (
    CodexRateLimitError,
    CodexRunnerError,
    CodexRunResult,
    extract_json_payload,
    run_codex,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_run(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[list[str], dict[str, Any]], subprocess.CompletedProcess[str]],
) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return handler(argv, kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace time.sleep with a recorder so backoff tests stay fast."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# ---------------------------------------------------------------------------
# extract_json_payload — lenient parser
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_plain_json_object_passes_through(self) -> None:
        raw = '{"verdict":"pass"}'
        assert extract_json_payload(raw) == {"verdict": "pass"}

    def test_json_object_with_surrounding_whitespace(self) -> None:
        raw = '   \n\n  {"verdict":"fail"}  \n '
        assert extract_json_payload(raw) == {"verdict": "fail"}

    def test_json_in_markdown_fence_with_lang(self) -> None:
        raw = (
            "Here is my verdict:\n\n"
            "```json\n"
            '{"verdict":"pass","summary":"ok"}\n'
            "```\n"
        )
        assert extract_json_payload(raw) == {
            "verdict": "pass",
            "summary": "ok",
        }

    def test_json_in_markdown_fence_without_lang(self) -> None:
        raw = "```\n" '{"verdict":"fail"}\n' "```"
        assert extract_json_payload(raw) == {"verdict": "fail"}

    def test_json_after_prose(self) -> None:
        raw = "Reviewing now.\n\n" '{"verdict":"pass","issues":[]}'
        assert extract_json_payload(raw) == {
            "verdict": "pass",
            "issues": [],
        }

    def test_first_object_wins_when_multiple(self) -> None:
        """If Codex emits two objects (rare), we take the first complete one."""
        raw = '{"verdict":"pass"}\n\n{"verdict":"fail"}'
        result = extract_json_payload(raw)
        assert result == {"verdict": "pass"}

    def test_nested_braces_preserved(self) -> None:
        raw = '{"a":{"b":{"c":1}},"d":[1,2,{"e":"f"}]}'
        assert extract_json_payload(raw) == {
            "a": {"b": {"c": 1}},
            "d": [1, 2, {"e": "f"}],
        }

    def test_unicode_preserved(self) -> None:
        raw = '{"summary":"тест с кириллицей"}'  # noqa: RUF001
        assert extract_json_payload(raw) == {
            "summary": "тест с кириллицей",  # noqa: RUF001
        }

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_payload("")

    def test_no_object_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_payload("just some prose")

    def test_malformed_object_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_payload('{"verdict": ')


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_codex_returns_parsed_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = '{"verdict":"pass","summary":"clean","verdict_confidence":0.9}'

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr="")

    _stub_run(monkeypatch, handler)
    result = run_codex(prompt="review", cwd=tmp_path)

    assert isinstance(result, CodexRunResult)
    assert result.parsed["verdict"] == "pass"
    assert result.retry_count == 0
    assert result.returncode == 0


def test_run_codex_strips_markdown_fences(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = "```json\n" '{"verdict":"fail"}\n' "```"

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr="")

    _stub_run(monkeypatch, handler)
    result = run_codex(prompt="review", cwd=tmp_path)
    assert result.parsed == {"verdict": "fail"}


def test_run_codex_builds_argv_with_exec_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"v":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="hello", cwd=tmp_path)

    argv, kwargs = calls[0]
    assert argv[0] == "codex"
    assert "exec" in argv
    assert "--json" in argv
    assert "hello" in argv
    assert kwargs.get("cwd") == tmp_path


def test_run_codex_inherits_env_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"v":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path)
    assert calls[0][1].get("env") is None


def test_run_codex_accepts_explicit_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"v":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    custom = {"OPENAI_API_KEY": "sk-test", "PATH": "/usr/bin"}
    run_codex(prompt="x", cwd=tmp_path, env=custom)
    assert calls[0][1].get("env") == custom


def test_run_codex_accepts_custom_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"v":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path, executable="/opt/codex/bin/codex")
    assert calls[0][0][0] == "/opt/codex/bin/codex"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_run_codex_raises_on_executable_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("codex not found")

    _stub_run(monkeypatch, handler)
    _no_sleep(monkeypatch)

    with pytest.raises(CodexRunnerError) as exc_info:
        run_codex(prompt="x", cwd=tmp_path)

    msg = str(exc_info.value).lower()
    assert "not found" in msg or "codex" in msg


def test_run_codex_raises_on_nonzero_exit_no_429(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-429 errors should fail fast, not retry."""

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=2, stdout="", stderr="auth error"
        )

    calls = _stub_run(monkeypatch, handler)
    _no_sleep(monkeypatch)

    with pytest.raises(CodexRunnerError) as exc_info:
        run_codex(prompt="x", cwd=tmp_path)

    assert exc_info.value.returncode == 2
    assert "auth error" in exc_info.value.stderr
    assert len(calls) == 1  # no retry


def test_run_codex_raises_on_unparseable_stdout_after_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Invalid JSON survives one retry then fails (matches AC-4 lenient + retry)."""

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=0, stdout="just prose, no json", stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    sleeps = _no_sleep(monkeypatch)

    with pytest.raises(CodexRunnerError) as exc_info:
        run_codex(prompt="x", cwd=tmp_path, max_json_retries=1)

    msg = str(exc_info.value).lower()
    assert "json" in msg or "parse" in msg
    assert len(calls) == 2  # 1 attempt + 1 retry
    assert sleeps == [1]  # one short pause between attempts


# ---------------------------------------------------------------------------
# Retry on 429 / network
# ---------------------------------------------------------------------------


def _rate_limited_then_ok(stderr: str) -> Callable[[list[str], dict[str, Any]], subprocess.CompletedProcess[str]]:
    """Builds a handler that fails with 429 the first N calls, then succeeds."""
    state = {"calls": 0}

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        state["calls"] += 1
        if state["calls"] <= 1:
            return subprocess.CompletedProcess(
                argv, returncode=1, stdout="", stderr=stderr
            )
        return subprocess.CompletedProcess(
            argv, returncode=0, stdout='{"verdict":"pass"}', stderr=""
        )

    return handler


def test_run_codex_retries_on_429(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = _rate_limited_then_ok(
        "Error 429: Rate limit exceeded. Retry-After: 2"
    )
    calls = _stub_run(monkeypatch, handler)
    sleeps = _no_sleep(monkeypatch)

    result = run_codex(prompt="x", cwd=tmp_path)

    assert result.parsed == {"verdict": "pass"}
    assert result.retry_count == 1
    assert len(calls) == 2
    # Honoured Retry-After hint (≥2s).
    assert sleeps and sleeps[0] >= 2


def test_run_codex_retries_use_default_backoff_when_no_retry_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = _rate_limited_then_ok("Error: 429 too many requests")
    _stub_run(monkeypatch, handler)
    sleeps = _no_sleep(monkeypatch)

    result = run_codex(prompt="x", cwd=tmp_path)
    assert result.retry_count == 1
    # Default backoff sequence starts at 1s.
    assert sleeps and sleeps[0] >= 1


def test_run_codex_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, returncode=1, stdout="", stderr="Error 429 rate limit"
        )

    calls = _stub_run(monkeypatch, handler)
    _no_sleep(monkeypatch)

    with pytest.raises(CodexRateLimitError) as exc_info:
        run_codex(prompt="x", cwd=tmp_path, max_rate_limit_retries=2)

    # 1 initial + 2 retries = 3 calls.
    assert len(calls) == 3
    assert exc_info.value.retry_count == 2


def test_run_codex_timeout_propagates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    _stub_run(monkeypatch, handler)
    _no_sleep(monkeypatch)

    with pytest.raises(CodexRunnerError) as exc_info:
        run_codex(prompt="x", cwd=tmp_path, timeout=5)

    msg = str(exc_info.value).lower()
    assert "timed out" in msg or "timeout" in msg


def test_run_codex_passes_timeout_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout='{"v":1}', stderr="")

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path, timeout=42)
    assert calls[0][1].get("timeout") == 42
