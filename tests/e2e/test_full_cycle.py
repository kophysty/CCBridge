"""End-to-end smoke test for the full CCBridge cycle.

Runs against the REAL ``codex`` CLI installed on the developer's
machine. Uses real network, real LLM tokens, real time (can take
1-3 minutes per call). Skipped by default — opt in with::

    pytest -m e2e

Requirements:

* ``codex`` 0.125.0+ in PATH, authenticated, ``OPENAI_API_KEY`` set.
* ``git`` in PATH.
* CCBridge installed (``uv pip install -e .`` or equivalent).

These tests do NOT call ``claude`` — orchestrator currently does not
invoke Claude directly (Claude is the entity initiating the Stop hook,
not something the audit cycle calls). The cycle exercised is git diff
→ codex review → verdict → state.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _have_real_codex() -> bool:
    """Skip e2e suite if codex isn't on PATH (CI, fresh dev box)."""
    return shutil.which("codex") is not None


SKIP_REASON_NO_CODEX = "real `codex` not found in PATH; skipping e2e"


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one committed file and one diff,
    matching what we tested manually in PR2b smoke.
    """
    repo = tmp_path / "e2e-proj"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "e2e@test"],
        ["git", "config", "user.name", "e2e"],
        ["git", "config", "core.autocrlf", "false"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "app.py"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "app.py").write_text(
        'def add(a, b):\n    """Add two numbers."""\n    return a + b\n',
        encoding="utf-8",
    )
    return repo


@pytest.mark.skipif(not _have_real_codex(), reason=SKIP_REASON_NO_CODEX)
def test_e2e_full_audit_cycle(tmp_repo: Path) -> None:
    """Real cycle: ccbridge init → audit run → inspect outcome / log.

    We don't assert on which terminal verdict we land — that depends
    on what the real model decides. We DO assert: exit 0, JSON shape,
    audit.jsonl populated, no ANSI in stdout, lockfile released.
    """
    init_result = subprocess.run(
        ["ccbridge", "init", str(tmp_repo), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    init_data = json.loads(init_result.stdout)
    assert init_data["settings_patched"] is True
    assert (tmp_repo / ".ccbridge" / "identity.json").exists()
    assert (tmp_repo / ".ccbridge" / "config.toml").exists()
    assert (tmp_repo / ".claude" / "settings.json").exists()

    run_result = subprocess.run(
        ["ccbridge", "audit", "run", "--project", str(tmp_repo), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=600,
    )
    assert run_result.returncode == 0, (
        f"audit run failed: stderr={run_result.stderr[:500]}"
    )
    outcome = json.loads(run_result.stdout)
    assert isinstance(outcome, dict)
    assert "run_uuid" in outcome
    assert outcome["final_verdict"] in {
        "pass",
        "fail",
        "needs_human",
        "error",
        "skipped",
    }
    assert outcome["iterations_used"] >= 1
    assert "\x1b[" not in run_result.stdout

    audit_path = tmp_repo / ".ccbridge" / "audit.jsonl"
    assert audit_path.exists()
    event_types: list[str] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        try:
            event_types.append(json.loads(line)["event_type"])
        except (json.JSONDecodeError, KeyError):
            pass
    assert "started" in event_types
    assert "iteration_complete" in event_types
    assert "verdict" in event_types or "error" in event_types

    assert not (tmp_repo / ".ccbridge" / "lockfile").exists()


@pytest.mark.skipif(not _have_real_codex(), reason=SKIP_REASON_NO_CODEX)
def test_e2e_audit_list_after_run(tmp_repo: Path) -> None:
    subprocess.run(
        ["ccbridge", "init", str(tmp_repo)],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["ccbridge", "audit", "run", "--project", str(tmp_repo), "--json"],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=600,
    )
    list_result = subprocess.run(
        ["ccbridge", "audit", "list", "--project", str(tmp_repo), "--json"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    runs = json.loads(list_result.stdout)
    assert isinstance(runs, list)
    assert len(runs) == 1


@pytest.mark.skipif(not _have_real_codex(), reason=SKIP_REASON_NO_CODEX)
def test_e2e_uninstall_cleans_up(tmp_repo: Path) -> None:
    subprocess.run(
        ["ccbridge", "init", str(tmp_repo)],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert (tmp_repo / ".ccbridge").exists()

    subprocess.run(
        ["ccbridge", "uninstall", str(tmp_repo), "--yes"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert not (tmp_repo / ".ccbridge").exists()
    settings_path = tmp_repo / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        for entry in data.get("hooks", {}).get("Stop", []):
            for h in entry.get("hooks", []):
                assert "ccbridge stop-hook" not in h.get("command", "")
