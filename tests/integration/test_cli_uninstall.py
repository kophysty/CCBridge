"""Integration tests for ``ccbridge uninstall`` (PR2b step 6b).

Decisions per audit feedback:

* Reverse of init:
  - Read settings.json
  - Remove the ccbridge entry from hooks.Stop (identified by command
    substring)
  - If hooks.Stop becomes empty, remove the key
  - If settings.json becomes empty AND a backup exists, restore from
    backup; otherwise delete settings.json (leave only what we
    created).
* ``.ccbridge/`` removal requires --yes confirmation OR --keep-data
  to skip removal entirely. Default is "ask" — but since ``input()``
  in a CliRunner test is awkward, we make confirmation explicit via
  flag (Click's ``--yes/--no`` style is the standard).
* ``--keep-data`` MUST coexist: removes hook entry but keeps
  ``.ccbridge/`` (audit history is valuable, user may want to
  re-init later or just inspect history).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ccbridge.cli import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _initialized_project(tmp_path: Path, with_existing_hook: bool = False) -> Path:
    """Run ``ccbridge init`` on a tmp_path project. Optionally add an
    unrelated user hook before init so we can verify it survives.
    """
    project = tmp_path / "proj"
    project.mkdir()
    if with_existing_hook:
        claude_dir = project / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo user-hook",
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
    return project


# ---------------------------------------------------------------------------
# Hook removal
# ---------------------------------------------------------------------------


def test_uninstall_removes_ccbridge_stop_hook_entry(tmp_path: Path) -> None:
    project = _initialized_project(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", str(project), "--yes"])

    assert result.exit_code == 0, result.output
    settings_path = project / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        cmds: list[str] = []
        for entry in data.get("hooks", {}).get("Stop", []):
            for hook in entry.get("hooks", []):
                cmds.append(hook.get("command", ""))
        assert not any("ccbridge stop-hook" in c for c in cmds)


def test_uninstall_preserves_user_hooks(tmp_path: Path) -> None:
    project = _initialized_project(tmp_path, with_existing_hook=True)

    runner = CliRunner()
    runner.invoke(cli, ["uninstall", str(project), "--yes"])

    data = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    cmds: list[str] = []
    for entry in data.get("hooks", {}).get("Stop", []):
        for hook in entry.get("hooks", []):
            cmds.append(hook.get("command", ""))
    assert any("echo user-hook" in c for c in cmds), (
        "user hook must survive uninstall"
    )
    assert not any("ccbridge stop-hook" in c for c in cmds)


def test_uninstall_collapses_empty_stop_array(tmp_path: Path) -> None:
    """If hooks.Stop becomes empty after our entry is removed, the key
    is dropped to keep settings.json tidy.
    """
    project = _initialized_project(tmp_path)

    runner = CliRunner()
    runner.invoke(cli, ["uninstall", str(project), "--yes"])

    settings_path = project / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        # Either Stop key removed or settings.json fully removed.
        assert "Stop" not in data.get("hooks", {})


def test_uninstall_restores_from_backup_when_settings_empty(
    tmp_path: Path,
) -> None:
    """If settings.json after cleanup would be effectively empty AND
    we have a backup, restore the backup (the user's pre-init state).
    """
    project = _initialized_project(tmp_path, with_existing_hook=False)
    # init created settings.json from scratch (no pre-existing) — so
    # the backup file should NOT exist, AND uninstall should leave
    # settings.json gone.

    runner = CliRunner()
    runner.invoke(cli, ["uninstall", str(project), "--yes"])

    settings_path = project / ".claude" / "settings.json"
    backup = project / ".claude" / "settings.json.ccbridge.bak"
    # Either the file is gone or it has no ccbridge content.
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data.get("hooks", {}).get("Stop", []) == []
    # No backup was created (init had no pre-existing file to back up).
    assert not backup.exists()


# ---------------------------------------------------------------------------
# .ccbridge/ removal
# ---------------------------------------------------------------------------


def test_uninstall_yes_removes_ccbridge_dir(tmp_path: Path) -> None:
    project = _initialized_project(tmp_path)
    assert (project / ".ccbridge").exists()

    runner = CliRunner()
    runner.invoke(cli, ["uninstall", str(project), "--yes"])

    assert not (project / ".ccbridge").exists()


def test_uninstall_keep_data_preserves_ccbridge_dir(tmp_path: Path) -> None:
    project = _initialized_project(tmp_path)
    audit_path = project / ".ccbridge" / "audit.jsonl"
    audit_path.write_text('{"event_type":"started","run_uuid":"r1"}\n', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli, ["uninstall", str(project), "--keep-data"]
    )

    assert result.exit_code == 0
    # .ccbridge/ stays (audit history preserved).
    assert (project / ".ccbridge").exists()
    assert audit_path.exists()
    # But hook entry IS removed.
    settings_path = project / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        cmds: list[str] = []
        for entry in data.get("hooks", {}).get("Stop", []):
            for hook in entry.get("hooks", []):
                cmds.append(hook.get("command", ""))
        assert not any("ccbridge stop-hook" in c for c in cmds)


def test_uninstall_without_yes_or_keep_data_aborts(tmp_path: Path) -> None:
    """Default safety: without --yes or --keep-data, uninstall refuses
    to proceed (would otherwise prompt; in non-tty / scripted contexts
    we want a clear "use --yes" error rather than a hang or accidental
    delete).
    """
    project = _initialized_project(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", str(project)])

    assert result.exit_code != 0
    assert (project / ".ccbridge").exists(), (
        ".ccbridge must NOT be deleted without explicit consent"
    )


# ---------------------------------------------------------------------------
# Idempotency / non-existent
# ---------------------------------------------------------------------------


def test_uninstall_on_uninitialized_project_is_noop(tmp_path: Path) -> None:
    """If there's nothing to uninstall, succeed quietly — don't error."""
    project = tmp_path / "proj"
    project.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", str(project), "--yes"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_uninstall_json_output(tmp_path: Path) -> None:
    project = _initialized_project(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["uninstall", str(project), "--yes", "--json"]
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data.get("project_dir") == str(project.resolve())
    assert data.get("settings_modified") is True
    assert data.get("ccbridge_dir_removed") is True
