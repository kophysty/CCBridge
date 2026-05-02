"""Integration tests for ``ccbridge init`` (PR2b step 6b).

Decisions per audit feedback:

1. ``init`` creates: ``.ccbridge/`` directory, ``identity.json``,
   ``config.toml`` template, ``.ccbridge/.gitignore``, and patches
   ``.claude/settings.json`` to add a Stop hook entry.
2. Settings merge: backup existing settings.json first (suffix
   ``.ccbridge.bak``), then add a Stop hook entry; never replace
   existing entries.
3. Hook entry uses ``ccbridge stop-hook`` (NOT ``ccbridge audit run``).
4. Idempotent: skip if already initialized unless ``--force``.
5. Rollback: create ``.ccbridge/`` first, then backup+patch settings;
   if patch fails, restore settings from backup. ``.ccbridge/`` is
   left in place — it doesn't break Claude.
6. Order matters: identity → config → gitignore → settings (settings
   last so any failure prevents Claude from invoking a half-set-up
   hook).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ccbridge.cli import cli
from ccbridge.core.state import load_identity

# ---------------------------------------------------------------------------
# Happy path: brand-new project
# ---------------------------------------------------------------------------


def test_init_creates_ccbridge_directory(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])

    assert result.exit_code == 0, result.output
    ccbridge_dir = project / ".ccbridge"
    assert ccbridge_dir.is_dir()


def test_init_creates_identity_with_uuid(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    identity = load_identity(project / ".ccbridge" / "identity.json")
    assert identity is not None
    # UUIDs are 36 chars with 4 hyphens.
    assert len(identity.project_id) == 36
    assert identity.project_id.count("-") == 4


def test_init_creates_config_toml_template(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    config_path = project / ".ccbridge" / "config.toml"
    assert config_path.exists()
    text = config_path.read_text(encoding="utf-8")
    # Must mention key sections we expose so users see what's tunable.
    assert "[project]" in text
    assert "[review]" in text
    assert "[codex]" in text or "[claude]" in text


def test_init_creates_ccbridge_gitignore(tmp_path: Path) -> None:
    """The .ccbridge/.gitignore keeps lockfile/state/audit out of git
    while still allowing config.toml and identity.json to be optionally
    committed if the team wants.
    """
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    gitignore = project / ".ccbridge" / ".gitignore"
    assert gitignore.exists()
    text = gitignore.read_text(encoding="utf-8")
    # Runtime artefacts must be ignored.
    assert "lockfile" in text
    assert "state.json" in text
    assert "audit.jsonl" in text


# ---------------------------------------------------------------------------
# .claude/settings.json patching
# ---------------------------------------------------------------------------


def test_init_creates_settings_json_when_absent(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    settings = project / ".claude" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert "hooks" in data
    assert "Stop" in data["hooks"]
    stop_hooks = data["hooks"]["Stop"]
    assert isinstance(stop_hooks, list)
    assert len(stop_hooks) >= 1


def test_init_stop_hook_entry_uses_stop_hook_subcommand(
    tmp_path: Path,
) -> None:
    """The hook entry runs ``ccbridge stop-hook``, NOT ``ccbridge audit
    run`` — only the former honours the stdin/stdout decision contract
    Claude expects.
    """
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    found = False
    for entry in data["hooks"]["Stop"]:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if "ccbridge stop-hook" in cmd:
                found = True
                # Must NOT use audit run as the hook command.
                assert "audit run" not in cmd
    assert found, f"no ccbridge stop-hook entry in {data}"


def test_init_preserves_existing_stop_hooks(tmp_path: Path) -> None:
    """If .claude/settings.json already has a Stop hook (e.g. user has
    their own hook), our entry is APPENDED, not REPLACING.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    existing = {
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": "echo user-hook"}
                    ],
                }
            ]
        },
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(existing), encoding="utf-8"
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (claude_dir / "settings.json").read_text(encoding="utf-8")
    )
    # Both entries present.
    cmds: list[str] = []
    for entry in data["hooks"]["Stop"]:
        for hook in entry.get("hooks", []):
            cmds.append(hook.get("command", ""))
    assert any("echo user-hook" in c for c in cmds), "user hook removed"
    assert any("ccbridge stop-hook" in c for c in cmds), "ccbridge hook missing"


def test_init_preserves_unrelated_settings_keys(tmp_path: Path) -> None:
    """Only the hooks.Stop array is touched; everything else stays."""
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    existing = {
        "model": "claude-opus",
        "permissions": {"allow": ["Bash", "Read"]},
        "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": []}]},
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(existing), encoding="utf-8"
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (claude_dir / "settings.json").read_text(encoding="utf-8")
    )
    assert data["model"] == "claude-opus"
    assert data["permissions"] == {"allow": ["Bash", "Read"]}
    assert "PostToolUse" in data["hooks"]


def test_init_creates_settings_backup(tmp_path: Path) -> None:
    """Backup .claude/settings.json.ccbridge.bak with the original content."""
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    original = {"model": "x", "hooks": {}}
    (claude_dir / "settings.json").write_text(
        json.dumps(original), encoding="utf-8"
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    backup = claude_dir / "settings.json.ccbridge.bak"
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_init_no_backup_when_no_existing_settings(tmp_path: Path) -> None:
    """If settings.json didn't exist, there's nothing to back up. The
    .bak file should NOT be created (would be misleading).
    """
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    backup = project / ".claude" / "settings.json.ccbridge.bak"
    assert not backup.exists()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_init_skips_if_already_initialized(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    first = runner.invoke(cli, ["init", str(project)])
    assert first.exit_code == 0
    identity_first = load_identity(project / ".ccbridge" / "identity.json")
    assert identity_first is not None

    second = runner.invoke(cli, ["init", str(project)])
    # Second invocation succeeds (or warns) but does NOT regenerate.
    assert second.exit_code == 0
    identity_second = load_identity(project / ".ccbridge" / "identity.json")
    assert identity_second is not None
    assert identity_second.project_id == identity_first.project_id, (
        "idempotent init must not regenerate project_id"
    )


def test_init_force_recreates(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    identity_first = load_identity(project / ".ccbridge" / "identity.json")
    assert identity_first is not None

    second = runner.invoke(cli, ["init", str(project), "--force"])
    assert second.exit_code == 0
    identity_second = load_identity(project / ".ccbridge" / "identity.json")
    assert identity_second is not None
    # --force regenerates identity (new UUID).
    assert identity_second.project_id != identity_first.project_id


def test_init_idempotent_does_not_duplicate_stop_hook_entry(
    tmp_path: Path,
) -> None:
    """Running init twice must not add the ccbridge entry twice."""
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    ccbridge_count = 0
    for entry in data["hooks"]["Stop"]:
        for hook in entry.get("hooks", []):
            if "ccbridge stop-hook" in hook.get("command", ""):
                ccbridge_count += 1
    assert ccbridge_count == 1, (
        f"expected 1 ccbridge entry, got {ccbridge_count}"
    )


# ---------------------------------------------------------------------------
# Rollback on settings patch failure
# ---------------------------------------------------------------------------


def test_init_rollback_restores_settings_on_patch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the atomic write to settings.json fails AFTER backup, restore
    from backup so user's Claude is not left with a corrupted file.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    original = {"model": "claude-3-opus", "hooks": {"Stop": []}}
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    # Patch the atomic write helper to fail.
    from ccbridge import cli as cli_mod

    def boom(path: Path, payload: dict[str, Any]) -> None:
        raise OSError("simulated atomic write failure")

    monkeypatch.setattr(cli_mod, "_atomic_write_json", boom)

    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])

    # init reports failure with non-zero exit.
    assert result.exit_code != 0
    # Settings restored to original content from backup.
    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored == original


# ---------------------------------------------------------------------------
# Project resolution + missing dir
# ---------------------------------------------------------------------------


def test_init_creates_project_dir_if_missing(tmp_path: Path) -> None:
    """If <path> doesn't exist, ``init`` creates it. Convenience for
    fresh projects."""
    project = tmp_path / "fresh-project"
    assert not project.exists()

    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])

    assert result.exit_code == 0
    assert (project / ".ccbridge").is_dir()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_init_json_output(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project), "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data.get("project_dir") == str(project.resolve())
    assert "project_id" in data
    assert data.get("settings_patched") is True
    assert data.get("settings_backed_up") is False
