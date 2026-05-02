"""Integration tests for codex_runner.

Codex 0.125.0 ``codex exec --json`` prints a JSONL event stream to
stdout, NOT a single JSON object. The verdict lives in the
``item.completed`` event under ``item.text`` (escaped string). See
``tests/fixtures/codex_event_stream.jsonl`` for the canonical shape.

Security boundary (ADR-002 / OWASP A03 / A04):

* Prompt is passed via stdin, NOT via argv. This avoids command-line
  length limits on Windows AND eliminates a class of injection
  vectors where a crafted prompt could be interpreted as additional
  argv tokens by a buggy shell wrapper.
* ``--sandbox read-only`` is mandatory in argv. CCBridge uses Codex
  as an *auditor*; it must not be able to mutate the workspace,
  even if a tool call in its agent loop tries to.

We never invoke a real codex binary in tests; subprocess.run is
monkeypatched. The fixture file pins the contract against the live
output we observed in audit (handoff-pr2a-audit.md, finding #1).
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
    extract_verdict_from_event_stream,
    run_codex,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
EVENT_STREAM_FIXTURE = (FIXTURE_DIR / "codex_event_stream.jsonl").read_text(
    encoding="utf-8"
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
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _event_stream(verdict_text: str) -> str:
    """Build a minimal valid event stream wrapping a verdict text payload."""
    import json as _json

    return "\n".join(
        [
            '{"type":"thread.started","thread_id":"T"}',
            '{"type":"turn.started"}',
            _json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "agent_message",
                        "text": verdict_text,
                    },
                }
            ),
            '{"type":"turn.completed","usage":{"input_tokens":1}}',
        ]
    )


# ---------------------------------------------------------------------------
# extract_verdict_from_event_stream — the new layer
# ---------------------------------------------------------------------------


class TestExtractVerdictFromEventStream:
    def test_happy_path_full_fixture(self) -> None:
        """The canonical live-CLI fixture extracts to a Verdict-shaped JSON."""
        verdict_text = extract_verdict_from_event_stream(EVENT_STREAM_FIXTURE)
        assert verdict_text.startswith("{")
        # The payload is a JSON string; parsing it should yield a dict
        # with a verdict field.
        import json

        parsed = json.loads(verdict_text)
        assert parsed["verdict"] == "pass"
        assert parsed["schema_version"] == 1

    def test_takes_last_agent_message_when_multiple(self) -> None:
        """If the agent emits multiple messages, the LAST one is the verdict."""
        import json as _json

        stream = "\n".join(
            [
                '{"type":"thread.started","thread_id":"T"}',
                _json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_0",
                            "type": "agent_message",
                            "text": "intermediate thought",
                        },
                    }
                ),
                _json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_1",
                            "type": "agent_message",
                            "text": '{"verdict":"final"}',
                        },
                    }
                ),
                '{"type":"turn.completed","usage":{}}',
            ]
        )
        assert (
            extract_verdict_from_event_stream(stream) == '{"verdict":"final"}'
        )

    def test_partial_last_line_skipped(self) -> None:
        """Torn write at end of stream — partial JSON line is skipped, not raised."""
        stream = (
            EVENT_STREAM_FIXTURE.rstrip("\n")
            + "\n"
            + '{"type":"turn.completed","us'  # truncated
        )
        # Should still find the agent_message that came earlier.
        verdict_text = extract_verdict_from_event_stream(stream)
        assert "schema_version" in verdict_text

    def test_empty_stream_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_verdict_from_event_stream("")

    def test_no_agent_message_raises(self) -> None:
        """Stream that never produced an agent_message — Codex returned
        only structural events. This is a real error, not a parsing bug.
        """
        stream = "\n".join(
            [
                '{"type":"thread.started","thread_id":"T"}',
                '{"type":"turn.started"}',
                '{"type":"turn.completed","usage":{}}',
            ]
        )
        with pytest.raises(ValueError) as exc_info:
            extract_verdict_from_event_stream(stream)
        assert "agent_message" in str(exc_info.value).lower()

    def test_error_event_in_stream_raises(self) -> None:
        """Codex sometimes emits an error event mid-stream (e.g. tool
        failure). We surface this as a ValueError so the runner converts
        it to CodexRunnerError.
        """
        stream = "\n".join(
            [
                '{"type":"thread.started","thread_id":"T"}',
                '{"type":"turn.started"}',
                '{"type":"error","message":"sandbox violation"}',
            ]
        )
        with pytest.raises(ValueError) as exc_info:
            extract_verdict_from_event_stream(stream)
        msg = str(exc_info.value).lower()
        assert "error" in msg or "sandbox violation" in msg.lower()

    def test_skips_non_agent_message_item_completed(self) -> None:
        """item.completed events with item.type != agent_message are skipped
        (e.g. tool_call, function_call). Only agent_message carries the verdict.
        """
        import json as _json

        stream = "\n".join(
            [
                '{"type":"thread.started","thread_id":"T"}',
                _json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_0",
                            "type": "tool_call",
                            "name": "ls",
                        },
                    }
                ),
                _json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "item_1",
                            "type": "agent_message",
                            "text": '{"verdict":"the_real_one"}',
                        },
                    }
                ),
                '{"type":"turn.completed","usage":{}}',
            ]
        )
        assert (
            extract_verdict_from_event_stream(stream)
            == '{"verdict":"the_real_one"}'
        )


# ---------------------------------------------------------------------------
# extract_json_payload — kept for the inner layer (verdict text → dict).
# This is the existing lenient parser; agent_message.text may itself be
# wrapped in markdown fences by the LLM.
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_plain_json_object(self) -> None:
        assert extract_json_payload('{"verdict":"pass"}') == {"verdict": "pass"}

    def test_markdown_fence_with_lang(self) -> None:
        raw = "```json\n" '{"verdict":"pass"}\n' "```"
        assert extract_json_payload(raw) == {"verdict": "pass"}

    def test_markdown_fence_without_lang(self) -> None:
        raw = "```\n" '{"verdict":"fail"}\n' "```"
        assert extract_json_payload(raw) == {"verdict": "fail"}

    def test_unicode_preserved(self) -> None:
        raw = '{"summary":"тест с кириллицей"}'  # noqa: RUF001
        assert extract_json_payload(raw) == {  # noqa: RUF001
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
# run_codex — happy path
# ---------------------------------------------------------------------------


def test_run_codex_returns_parsed_verdict_from_event_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    _stub_run(monkeypatch, handler)
    result = run_codex(prompt="review", cwd=tmp_path)

    assert isinstance(result, CodexRunResult)
    assert result.parsed["verdict"] == "pass"
    assert result.parsed["schema_version"] == 1
    assert result.retry_count == 0
    assert result.returncode == 0


def test_run_codex_handles_agent_message_with_markdown_fence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex's own agent_message.text MAY wrap the verdict in markdown
    fences (the LLM sometimes does this despite the system prompt).
    The lenient extract_json_payload deals with it.
    """
    fenced_verdict = '```json\\n{\\"verdict\\":\\"fail\\",\\"schema_version\\":1}\\n```'
    stream = _event_stream(fenced_verdict)

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=stream, stderr="")

    _stub_run(monkeypatch, handler)
    result = run_codex(prompt="review", cwd=tmp_path)
    assert result.parsed["verdict"] == "fail"


# ---------------------------------------------------------------------------
# run_codex — argv contract (security + size boundaries)
# ---------------------------------------------------------------------------


def test_run_codex_argv_does_not_contain_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prompt MUST go via stdin, not argv. Two reasons:
    1. Windows command-line length limit (~8K chars) — medium-context
       prompts (rules + diff) routinely exceed this.
    2. Defense in depth (OWASP A03): no shell can ever interpret the
       prompt as additional argv tokens, regardless of how subprocess
       is configured.
    """

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    secret_marker = "VERY_DISTINCTIVE_PROMPT_MARKER_X91"
    run_codex(prompt=secret_marker, cwd=tmp_path)

    argv, kwargs = calls[0]
    assert secret_marker not in " ".join(argv), (
        f"prompt leaked into argv: {argv}"
    )
    # And it must have arrived via stdin instead.
    assert kwargs.get("input") == secret_marker


def test_run_codex_argv_includes_sandbox_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-002 / OWASP A04: Codex acts as auditor and MUST run in
    read-only sandbox. Ensures it cannot mutate the workspace via
    tool calls even if its agent loop attempts to.
    """

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path)

    argv, _ = calls[0]
    # Two valid forms accepted by codex 0.125.0: --sandbox read-only or
    # -s read-only. We require the explicit --sandbox.
    assert "--sandbox" in argv
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "read-only"


def test_run_codex_argv_skeleton(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path)

    argv, kwargs = calls[0]
    assert argv[0] == "codex"
    assert "exec" in argv
    assert "--json" in argv
    assert kwargs.get("cwd") == tmp_path


# ---------------------------------------------------------------------------
# run_codex — env / executable
# ---------------------------------------------------------------------------


def test_run_codex_inherits_env_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path)
    assert calls[0][1].get("env") is None


def test_run_codex_accepts_explicit_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

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
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path, executable="/opt/codex/bin/codex")
    assert calls[0][0][0] == "/opt/codex/bin/codex"


# ---------------------------------------------------------------------------
# run_codex — failure modes
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
    assert len(calls) == 1


def test_run_codex_raises_when_stream_has_no_agent_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex returned exit 0 and a stream, but no agent_message in it.
    After max_json_retries we surface this as CodexRunnerError —
    distinct from a malformed JSON body inside agent_message.text.
    """
    empty_stream = "\n".join(
        [
            '{"type":"thread.started","thread_id":"T"}',
            '{"type":"turn.completed","usage":{}}',
        ]
    )

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=empty_stream, stderr="")

    calls = _stub_run(monkeypatch, handler)
    _no_sleep(monkeypatch)

    with pytest.raises(CodexRunnerError) as exc_info:
        run_codex(prompt="x", cwd=tmp_path, max_json_retries=1)

    msg = str(exc_info.value).lower()
    assert "agent_message" in msg or "no" in msg
    assert len(calls) == 2  # 1 attempt + 1 retry


def test_run_codex_raises_when_agent_message_text_is_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stream is well-formed and contains agent_message, but the inner
    text is not valid JSON. Same retry-once path; ultimately
    CodexRunnerError.
    """
    bad_stream = _event_stream("not valid json at all")

    def handler(
        argv: list[str], kwargs: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=bad_stream, stderr="")

    calls = _stub_run(monkeypatch, handler)
    _no_sleep(monkeypatch)

    with pytest.raises(CodexRunnerError):
        run_codex(prompt="x", cwd=tmp_path, max_json_retries=1)

    assert len(calls) == 2


# ---------------------------------------------------------------------------
# run_codex — retry on 429
# ---------------------------------------------------------------------------


def _rate_limited_then_ok(
    stderr: str,
) -> Callable[[list[str], dict[str, Any]], subprocess.CompletedProcess[str]]:
    """Builds a handler that fails with 429 the first call, then succeeds."""
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
            argv, returncode=0, stdout=EVENT_STREAM_FIXTURE, stderr=""
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

    assert result.parsed["verdict"] == "pass"
    assert result.retry_count == 1
    assert len(calls) == 2
    assert sleeps and sleeps[0] >= 2


def test_run_codex_default_backoff_when_no_retry_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = _rate_limited_then_ok("Error: 429 too many requests")
    _stub_run(monkeypatch, handler)
    sleeps = _no_sleep(monkeypatch)

    result = run_codex(prompt="x", cwd=tmp_path)
    assert result.retry_count == 1
    assert sleeps and sleeps[0] >= 1


def test_run_codex_gives_up_after_max_rate_limit_retries(
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

    assert len(calls) == 3
    assert exc_info.value.retry_count == 2


# ---------------------------------------------------------------------------
# run_codex — timeout
# ---------------------------------------------------------------------------


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
        return subprocess.CompletedProcess(
            argv, 0, stdout=EVENT_STREAM_FIXTURE, stderr=""
        )

    calls = _stub_run(monkeypatch, handler)
    run_codex(prompt="x", cwd=tmp_path, timeout=42)
    assert calls[0][1].get("timeout") == 42
