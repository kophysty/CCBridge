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
import sys
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
            if "stop-hook" in cmd:
                found = True
                # Must NOT use audit run as the hook command.
                assert "audit run" not in cmd
    assert found, f"no stop-hook entry in {data}"


def test_init_stop_hook_command_is_absolute_path(tmp_path: Path) -> None:
    """Audit finding #3: bare 'ccbridge stop-hook' is a PATH-hijack
    risk on Windows / unstable across venvs. The hook command must be
    an absolute path to the python interpreter that owns the project's
    ccbridge install, invoking ``python -m ccbridge.cli stop-hook``.
    """
    import os
    import sys

    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )

    cmd = ""
    for entry in data["hooks"]["Stop"]:
        for hook in entry.get("hooks", []):
            if "stop-hook" in hook.get("command", ""):
                cmd = hook["command"]
                break

    assert cmd, "no stop-hook entry"
    # Bare command must NOT appear.
    assert not cmd.startswith("ccbridge "), (
        f"hook command must not start with bare 'ccbridge': {cmd!r}"
    )
    # Must reference python -m ccbridge.cli with an absolute interpreter.
    assert "ccbridge.cli" in cmd, f"command should invoke ccbridge.cli: {cmd!r}"
    assert "stop-hook" in cmd
    # Extract the interpreter token (first token, possibly quoted).
    first_token = cmd.split()[0].strip('"').strip("'")
    assert os.path.isabs(first_token), (
        f"interpreter must be absolute path, got: {first_token!r}"
    )
    # Quoting: if the path contains spaces, must be quoted.
    if " " in first_token:
        assert cmd.startswith('"'), f"path with spaces must be quoted: {cmd!r}"
    # Sanity: the interpreter we resolved is the same Python that's
    # running this test (init was invoked via this interpreter, so it
    # writes its own sys.executable into the hook command).
    assert first_token == sys.executable or first_token.replace(
        "/", os.sep
    ) == sys.executable


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
    assert any("stop-hook" in c for c in cmds), "ccbridge stop-hook missing"


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
    """Backup .claude/settings.json.ccbridge.bak preserves the original
    user content (after sanitization — see Blocker #3 fix). User-only
    keys must survive verbatim; CCBridge-related keys, if any, are
    stripped before the backup is written.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    # Note: pre-CCBridge state with a user-only top-level key.
    original = {"model": "claude-3-opus"}
    (claude_dir / "settings.json").write_text(
        json.dumps(original), encoding="utf-8"
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    backup = claude_dir / "settings.json.ccbridge.bak"
    assert backup.exists()
    # User content fully preserved; CCBridge entries (none here) excluded.
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


def test_init_recognises_legacy_bare_entry_does_not_duplicate(
    tmp_path: Path,
) -> None:
    """Audit follow-up: a project initialized by an older CCBridge
    version has ``ccbridge stop-hook`` (bare) in settings.json. Running
    the new ``init`` must recognise it as ours and not add a second
    entry.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    legacy_settings = {
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": "ccbridge stop-hook"}
                    ],
                }
            ]
        }
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(legacy_settings), encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project)])
    assert result.exit_code == 0

    data = json.loads(
        (claude_dir / "settings.json").read_text(encoding="utf-8")
    )
    cmds: list[str] = []
    for entry in data["hooks"]["Stop"]:
        for hook in entry.get("hooks", []):
            cmds.append(hook.get("command", ""))
    # No duplication: still exactly one ccbridge-marked entry.
    ccbridge_cmds = [c for c in cmds if "stop-hook" in c]
    assert len(ccbridge_cmds) == 1, (
        f"expected 1 ccbridge entry after init-on-legacy, got "
        f"{len(ccbridge_cmds)}: {ccbridge_cmds}"
    )


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
            if "stop-hook" in hook.get("command", ""):
                ccbridge_count += 1
    assert ccbridge_count == 1, (
        f"expected 1 ccbridge entry, got {ccbridge_count}"
    )


# ---------------------------------------------------------------------------
# UserPromptSubmit hook (substep 5e)
# ---------------------------------------------------------------------------


def test_init_creates_user_prompt_submit_hook_entry(tmp_path: Path) -> None:
    """init must add a UserPromptSubmit entry alongside the Stop entry,
    so the prompt-hook subcommand sees the user's [skip-review] marker.
    """
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    assert "UserPromptSubmit" in data["hooks"]
    entries = data["hooks"]["UserPromptSubmit"]
    assert isinstance(entries, list)
    assert len(entries) >= 1

    # Must invoke ``... ccbridge.cli prompt-hook`` (not stop-hook).
    found = False
    for entry in entries:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if "prompt-hook" in cmd:
                found = True
                assert "ccbridge.cli prompt-hook" in cmd
    assert found, f"no prompt-hook entry in {data}"


def test_init_user_prompt_submit_hook_uses_absolute_python(
    tmp_path: Path,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    cmds = []
    for entry in data["hooks"]["UserPromptSubmit"]:
        for hook in entry.get("hooks", []):
            cmds.append(hook.get("command", ""))
    assert any(sys.executable in cmd for cmd in cmds), (
        f"prompt-hook command should use sys.executable; got {cmds}"
    )


def test_init_idempotent_does_not_duplicate_user_prompt_submit_entry(
    tmp_path: Path,
) -> None:
    """Double init must not add the prompt-hook entry twice."""
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    count = 0
    for entry in data["hooks"]["UserPromptSubmit"]:
        for hook in entry.get("hooks", []):
            if "prompt-hook" in hook.get("command", ""):
                count += 1
    assert count == 1


def test_init_preserves_existing_user_prompt_submit_hooks(
    tmp_path: Path,
) -> None:
    """If the user already has UserPromptSubmit entries (from another
    tool), init must keep them and ADD ours alongside.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "their-other-tool --foo",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    cmds = []
    for entry in data["hooks"]["UserPromptSubmit"]:
        for hook in entry.get("hooks", []):
            cmds.append(hook.get("command", ""))
    assert any("their-other-tool" in c for c in cmds)
    assert any("prompt-hook" in c for c in cmds)


# ---------------------------------------------------------------------------
# Backup poisoning protection (Blocker #3)
# ---------------------------------------------------------------------------
# Repro: init -> init --force -> uninstall --yes leaves CCBridge entries
# in the restored settings.json, because the second init created a
# backup containing the *post-CCBridge* state. Fix: sanitize backup
# content (strip our markers) before writing — the backup must always
# represent a CCBridge-free settings.json.


def test_force_init_then_uninstall_does_not_leave_ccbridge_entries(
    tmp_path: Path,
) -> None:
    """Repro for audit Blocker #3.

    init (creates fresh settings) → init --force (overwrite) →
    uninstall --yes. The restored state must NOT contain any CCBridge
    hooks; it should be pre-CCBridge state (or empty).
    """
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    runner.invoke(cli, ["init", str(project), "--force"])
    runner.invoke(cli, ["uninstall", str(project), "--yes"])

    settings_path = project / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        cmds: list[str] = []
        for event_entries in data.get("hooks", {}).values():
            if not isinstance(event_entries, list):
                continue
            for entry in event_entries:
                for hook in entry.get("hooks", []):
                    cmds.append(hook.get("command", ""))
        assert not any("ccbridge" in c for c in cmds), (
            f"CCBridge entries leaked through backup: {cmds}"
        )


def test_legacy_init_then_uninstall_does_not_restore_legacy_entry(
    tmp_path: Path,
) -> None:
    """Pre-existing legacy settings.json with bare ccbridge stop-hook
    entry. After init (which upgrades to absolute path) and uninstall,
    the legacy entry must NOT come back via backup restore.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    legacy = {
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": "ccbridge stop-hook"}
                    ],
                }
            ]
        }
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])
    runner.invoke(cli, ["uninstall", str(project), "--yes"])

    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        cmds: list[str] = []
        for event_entries in data.get("hooks", {}).values():
            if not isinstance(event_entries, list):
                continue
            for entry in event_entries:
                for hook in entry.get("hooks", []):
                    cmds.append(hook.get("command", ""))
        assert not any("ccbridge" in c.lower() for c in cmds), (
            f"legacy entry leaked through backup: {cmds}"
        )


def test_backup_does_not_contain_ccbridge_markers(tmp_path: Path) -> None:
    """The .ccbridge.bak file itself must never contain CCBridge entries
    — even if the source settings.json did. Backup must reflect a
    CCBridge-free state at all times.
    """
    project = tmp_path / "proj"
    project.mkdir()
    claude_dir = project / ".claude"
    claude_dir.mkdir()
    pre_ccbridge_with_legacy = {
        "model": "x",
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "command", "command": "ccbridge stop-hook"}
                    ],
                }
            ]
        },
    }
    (claude_dir / "settings.json").write_text(
        json.dumps(pre_ccbridge_with_legacy), encoding="utf-8"
    )

    runner = CliRunner()
    runner.invoke(cli, ["init", str(project)])

    backup = claude_dir / "settings.json.ccbridge.bak"
    if backup.exists():
        backup_data = json.loads(backup.read_text(encoding="utf-8"))
        cmds: list[str] = []
        for event_entries in backup_data.get("hooks", {}).values():
            if not isinstance(event_entries, list):
                continue
            for entry in event_entries:
                for hook in entry.get("hooks", []):
                    cmds.append(hook.get("command", ""))
        assert not any("ccbridge" in c.lower() for c in cmds), (
            f"backup contains CCBridge markers: {cmds}"
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
    # Use a pre-CCBridge state without empty hooks dict, since backup
    # sanitization (Blocker #3) drops empty hooks keys in the backup.
    original = {"model": "claude-3-opus"}
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
    # Settings restored to original content from (sanitized) backup.
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
