"""Configuration loading for CCBridge.

Hierarchy (lower → higher priority):

1. Hard-coded defaults (this module).
2. Global config: `~/.ccbridge/config.toml` (via `platformdirs`,
   so on Windows this resolves to `%APPDATA%/ccbridge/config.toml`).
3. Project config: `<project>/.ccbridge/config.toml`.

Loaded values are merged shallowly per top-level table. A project
setting fully overrides a global setting for the same key, but tables
that the project doesn't mention keep their global values.

Notes:

* TOML parsing uses `tomllib` (stdlib in 3.11+).
* UTF-8 BOM is stripped before parsing — Notepad on Windows is a
  common source of BOM-tainted config files.
* `extra` keys at the top level are tolerated (forward compat); within
  declared tables we error on unknown keys to catch typos early.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults (mirrors ARCHITECTURE.md §2.6 hard caps)
# ---------------------------------------------------------------------------


DEFAULT_PROJECT_NAME = "untitled"
DEFAULT_CONTEXT_LEVEL = "medium"
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_DIFF_LINES = 2000
DEFAULT_MAX_FILE_LINES = 1500
DEFAULT_MAX_TOTAL_TOKENS = 100_000
DEFAULT_INCLUDE_RECENT_AUDITS = 3
DEFAULT_VERDICT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_CODEX_MODEL = "gpt-4o"
DEFAULT_CODEX_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_CLAUDE_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_SKIP_MARKER = "[skip-review]"
DEFAULT_SKIP_TRIVIAL_DIFF_MAX_LINES = 0  # 0 = off; >0 = skip if diff ≤ N lines


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectSection:
    name: str = DEFAULT_PROJECT_NAME


@dataclass(frozen=True)
class ReviewSection:
    context_level: str = DEFAULT_CONTEXT_LEVEL
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES
    max_file_lines: int = DEFAULT_MAX_FILE_LINES
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS
    include_rules: tuple[str, ...] = ()
    include_recent_audits: int = DEFAULT_INCLUDE_RECENT_AUDITS
    verdict_confidence_threshold: float = DEFAULT_VERDICT_CONFIDENCE_THRESHOLD
    skip_marker: str = DEFAULT_SKIP_MARKER
    skip_trivial_diff_max_lines: int = DEFAULT_SKIP_TRIVIAL_DIFF_MAX_LINES


@dataclass(frozen=True)
class CodexSection:
    model: str = DEFAULT_CODEX_MODEL
    api_key_env: str = DEFAULT_CODEX_API_KEY_ENV


@dataclass(frozen=True)
class ClaudeSection:
    api_key_env: str = DEFAULT_CLAUDE_API_KEY_ENV


@dataclass(frozen=True)
class Config:
    project: ProjectSection = field(default_factory=ProjectSection)
    review: ReviewSection = field(default_factory=ReviewSection)
    codex: CodexSection = field(default_factory=CodexSection)
    claude: ClaudeSection = field(default_factory=ClaudeSection)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def global_config_path() -> Path:
    """Cross-platform path to the user-level config (`~/.ccbridge/config.toml`).

    On Windows this resolves to `%APPDATA%/ccbridge/config.toml`; on
    Linux to `~/.config/ccbridge/config.toml`; on macOS to
    `~/Library/Application Support/ccbridge/config.toml`.
    """
    return Path(platformdirs.user_config_dir("ccbridge")) / "config.toml"


def project_config_path(project_dir: Path) -> Path:
    """Where a project's config lives, relative to its root."""
    return project_dir / ".ccbridge" / "config.toml"


def load_config(project_dir: Path | None = None) -> Config:
    """Resolve the effective config for the given project.

    `project_dir=None` returns global+defaults only — useful for
    `ccbridge init` before a project config exists.
    """
    layered: dict[str, dict[str, Any]] = {}

    global_data = _load_toml_if_exists(global_config_path())
    if global_data:
        layered = _merge(layered, global_data)

    if project_dir is not None:
        project_data = _load_toml_if_exists(project_config_path(project_dir))
        if project_data:
            layered = _merge(layered, project_data)

    return _build_config(layered)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_toml_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    # Strip UTF-8 BOM if present — `tomllib.loads` does not.
    if raw.startswith(b"\xef\xbb\xbf"):
        logger.debug("stripping UTF-8 BOM from %s", path)
        raw = raw[3:]
    text = raw.decode("utf-8")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path} is not valid TOML: {exc}") from exc


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Shallow per-table merge: overlay's tables fully replace per-key,
    untouched tables remain from base.
    """
    merged: dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for table, value in overlay.items():
        if table in merged and isinstance(merged[table], dict) and isinstance(value, dict):
            merged[table].update(value)
        else:
            merged[table] = value
    return merged


def _build_config(data: dict[str, Any]) -> Config:
    return Config(
        project=_build_project(data.get("project", {})),
        review=_build_review(data.get("review", {})),
        codex=_build_codex(data.get("codex", {})),
        claude=_build_claude(data.get("claude", {})),
    )


def _build_project(d: dict[str, Any]) -> ProjectSection:
    _reject_unknown(d, ProjectSection, "project")
    return ProjectSection(name=str(d.get("name", DEFAULT_PROJECT_NAME)))


def _build_review(d: dict[str, Any]) -> ReviewSection:
    _reject_unknown(d, ReviewSection, "review")
    context_level = str(d.get("context_level", DEFAULT_CONTEXT_LEVEL))
    if context_level not in {"minimal", "medium", "full"}:
        raise ValueError(
            f"review.context_level must be minimal|medium|full, got {context_level!r}"
        )
    return ReviewSection(
        context_level=context_level,
        max_iterations=int(d.get("max_iterations", DEFAULT_MAX_ITERATIONS)),
        max_diff_lines=int(d.get("max_diff_lines", DEFAULT_MAX_DIFF_LINES)),
        max_file_lines=int(d.get("max_file_lines", DEFAULT_MAX_FILE_LINES)),
        max_total_tokens=int(d.get("max_total_tokens", DEFAULT_MAX_TOTAL_TOKENS)),
        include_rules=tuple(d.get("include_rules", ())),
        include_recent_audits=int(d.get("include_recent_audits", DEFAULT_INCLUDE_RECENT_AUDITS)),
        verdict_confidence_threshold=float(
            d.get("verdict_confidence_threshold", DEFAULT_VERDICT_CONFIDENCE_THRESHOLD)
        ),
        skip_marker=str(d.get("skip_marker", DEFAULT_SKIP_MARKER)),
        skip_trivial_diff_max_lines=int(
            d.get(
                "skip_trivial_diff_max_lines", DEFAULT_SKIP_TRIVIAL_DIFF_MAX_LINES
            )
        ),
    )


def _build_codex(d: dict[str, Any]) -> CodexSection:
    _reject_unknown(d, CodexSection, "codex")
    return CodexSection(
        model=str(d.get("model", DEFAULT_CODEX_MODEL)),
        api_key_env=str(d.get("api_key_env", DEFAULT_CODEX_API_KEY_ENV)),
    )


def _build_claude(d: dict[str, Any]) -> ClaudeSection:
    _reject_unknown(d, ClaudeSection, "claude")
    return ClaudeSection(
        api_key_env=str(d.get("api_key_env", DEFAULT_CLAUDE_API_KEY_ENV)),
    )


def _reject_unknown(d: dict[str, Any], cls: type, table_name: str) -> None:
    """Catch typos in config keys early.

    We accept the dataclass field names as the source of truth.
    """
    allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    unknown = set(d.keys()) - allowed
    if unknown:
        raise ValueError(
            f"unknown keys in [{table_name}]: {sorted(unknown)}; "
            f"valid keys are {sorted(allowed)}"
        )
