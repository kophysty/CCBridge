"""Integration tests for ccbridge.core.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from ccbridge.core import config as cfg


def test_load_with_no_files_returns_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the global config path into an empty location so we read
    # only defaults.
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no-such" / "config.toml")

    config = cfg.load_config(project_dir=tmp_path)

    assert config.project.name == cfg.DEFAULT_PROJECT_NAME
    assert config.review.context_level == cfg.DEFAULT_CONTEXT_LEVEL
    assert config.review.max_iterations == cfg.DEFAULT_MAX_ITERATIONS
    assert config.codex.api_key_env == cfg.DEFAULT_CODEX_API_KEY_ENV


def test_project_config_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")

    project_path = tmp_path / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        """
[project]
name = "MyProject"

[review]
context_level = "full"
max_iterations = 5
""",
        encoding="utf-8",
    )

    config = cfg.load_config(project_dir=tmp_path)
    assert config.project.name == "MyProject"
    assert config.review.context_level == "full"
    assert config.review.max_iterations == 5
    # Untouched keys stay at default.
    assert config.review.max_diff_lines == cfg.DEFAULT_MAX_DIFF_LINES


def test_project_config_overrides_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_path = tmp_path / "global" / "config.toml"
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        '[review]\ncontext_level = "minimal"\nmax_iterations = 7\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)

    project_path = tmp_path / "project" / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        '[review]\ncontext_level = "full"\n',
        encoding="utf-8",
    )

    config = cfg.load_config(project_dir=tmp_path / "project")
    # Project wins for `context_level`.
    assert config.review.context_level == "full"
    # Project doesn't override max_iterations → global value sticks.
    assert config.review.max_iterations == 7


def test_unknown_key_in_table_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")
    project_path = tmp_path / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        '[review]\ntpyo_key = 1\n',  # typo
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown keys in \\[review\\]"):
        cfg.load_config(project_dir=tmp_path)


def test_invalid_context_level_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")
    project_path = tmp_path / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        '[review]\ncontext_level = "deep"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be minimal\\|medium\\|full"):
        cfg.load_config(project_dir=tmp_path)


def test_invalid_toml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")
    project_path = tmp_path / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text("not = toml = at = all", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        cfg.load_config(project_dir=tmp_path)


def test_bom_is_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Notepad on Windows may save TOML with UTF-8 BOM."""
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")
    project_path = tmp_path / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)

    body = '[project]\nname = "WithBOM"\n'
    project_path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    config = cfg.load_config(project_dir=tmp_path)
    assert config.project.name == "WithBOM"


def test_load_config_with_no_project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ccbridge init` calls this before a project config exists."""
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")
    config = cfg.load_config(project_dir=None)
    assert config.review.context_level == cfg.DEFAULT_CONTEXT_LEVEL


def test_include_rules_parsed_as_tuple(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "no" / "config.toml")
    project_path = tmp_path / ".ccbridge" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        '[review]\ninclude_rules = ["Rulebook/R-*.md", "CLAUDE.md"]\n',
        encoding="utf-8",
    )
    config = cfg.load_config(project_dir=tmp_path)
    assert config.review.include_rules == ("Rulebook/R-*.md", "CLAUDE.md")


def test_global_config_path_uses_platformdirs() -> None:
    """Sanity: the function returns a sensible per-OS path, not /home/x on Windows."""
    path = cfg.global_config_path()
    assert path.name == "config.toml"
    assert path.parent.name == "ccbridge"


def test_project_config_path_layout() -> None:
    project = Path("/tmp/whatever")
    assert cfg.project_config_path(project) == project / ".ccbridge" / "config.toml"
