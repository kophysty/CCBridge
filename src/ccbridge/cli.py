"""CCBridge command-line interface.

PR2b step 6a: read/run-only commands. ``init`` / ``uninstall`` are
declared but stub out with a clear "deferred to 6b" error so users
don't get a confusing "no such command" if they try too early.

Output contract:

* **Default (human)**: rich-formatted output to stdout. Diagnostics
  (warnings, non-fatal errors) → stderr.
* **--json**: stdout is **strictly** a single valid JSON document.
  No ANSI, no banners, no "press any key". Diagnostics still go to
  stderr only. Scripts and downstream parsers depend on this.

Project resolution (per audit feedback):

1. ``--project PATH`` is the override.
2. Otherwise: ``git rev-parse --show-toplevel`` from cwd, so running
   from a subdirectory still puts ``.ccbridge/`` at the repo root.
3. Fallback to cwd if not in a git repo.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Force stdout/stderr to UTF-8 BEFORE rich/click are imported. Default
# Windows console encoding is cp1251 / cp1252 / whatever the locale
# dictates. ``ccbridge`` outputs Unicode (rich glyphs ↔ → ✻ plus
# cyrillic in prompts). Without this, ``ccbridge --help`` crashes on
# Russian Windows because Click's help text contains ``↔``.
#
# Best-effort: if either stream is non-reconfigurable (rare; some
# shell redirects), silently skip.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

import click  # noqa: E402  — must come after stdio reconfigure
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from ccbridge.core.audit_log import AuditLog  # noqa: E402
from ccbridge.core.event_bus import EventBus  # noqa: E402
from ccbridge.core.events import (  # noqa: E402
    CCBridgeEvent,
    IterationCompleteEvent,
    VerdictEvent,
)
from ccbridge.core.lockfile import LockBusyError, LockHolder  # noqa: E402
from ccbridge.core.orchestrator import OrchestratorOutcome, run_audit  # noqa: E402
from ccbridge.renderers.rich_renderer import RichRenderer  # noqa: E402
from ccbridge.transports.audit_watch import watch_audit_log  # noqa: E402
from ccbridge.transports.stop_hook import stop_hook_main  # noqa: E402

CCBRIDGE_DIR_NAME = ".ccbridge"


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


def _resolve_project(explicit: str | None) -> Path:
    """Resolve project root per the documented hierarchy."""
    if explicit is not None:
        return Path(explicit).resolve()

    cwd = Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return cwd.resolve()

    if result.returncode == 0:
        toplevel = result.stdout.strip()
        if toplevel:
            return Path(toplevel).resolve()

    return cwd.resolve()


# ---------------------------------------------------------------------------
# Run summary helpers (group audit.jsonl events into per-run dicts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSummary:
    run_uuid: str
    final_verdict: str | None
    iterations_used: int
    last_summary: str
    duration_sec: float
    total_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _group_runs(events: Iterable[CCBridgeEvent]) -> list[RunSummary]:
    """Collapse the event stream into one summary per run_uuid.

    Order: oldest run first. Within a run, the IterationCompleteEvent
    (if present) drives final_verdict / iterations_used / cost; we fall
    back to the last VerdictEvent if the run never completed cleanly.
    """
    by_run: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for e in events:
        if e.run_uuid not in by_run:
            by_run[e.run_uuid] = {"events": []}
            order.append(e.run_uuid)
        by_run[e.run_uuid]["events"].append(e)

    summaries: list[RunSummary] = []
    for run_uuid in order:
        bucket = by_run[run_uuid]
        bucket_events: list[CCBridgeEvent] = bucket["events"]

        complete = next(
            (
                e
                for e in reversed(bucket_events)
                if isinstance(e, IterationCompleteEvent)
            ),
            None,
        )
        last_verdict = next(
            (e for e in reversed(bucket_events) if isinstance(e, VerdictEvent)),
            None,
        )

        if complete is not None:
            final = complete.final_verdict
            iterations = complete.iterations_used
            cost = complete.total_cost_usd
            duration = complete.duration_sec
        elif last_verdict is not None:
            final = last_verdict.verdict
            iterations = 1
            cost = last_verdict.cost_usd
            duration = last_verdict.duration_sec
        else:
            final = None
            iterations = 0
            cost = 0.0
            duration = 0.0

        last_summary = (
            last_verdict.summary if last_verdict is not None else ""
        )

        summaries.append(
            RunSummary(
                run_uuid=run_uuid,
                final_verdict=final,
                iterations_used=iterations,
                last_summary=last_summary,
                duration_sec=duration,
                total_cost_usd=cost,
            )
        )

    return summaries


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(package_name="ccbridge", message="%(version)s")
def cli() -> None:
    """CCBridge — automate Claude Code ↔ Codex peer review."""


# ---------------------------------------------------------------------------
# `audit` subgroup
# ---------------------------------------------------------------------------


@cli.group()
def audit() -> None:
    """Audit-related commands (run, list, get, watch)."""


@audit.command("run")
@click.option(
    "--project",
    "project_opt",
    default=None,
    help="Project root. Defaults to git toplevel of cwd, or cwd.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit only the final OrchestratorOutcome JSON to stdout.",
)
def audit_run(project_opt: str | None, as_json: bool) -> None:
    """Run one peer-review cycle on the project."""
    project = _resolve_project(project_opt)
    ccbridge_dir = project / CCBRIDGE_DIR_NAME

    bus = EventBus()
    if not as_json:
        bus.subscribe(RichRenderer())  # stdout — terminal user
    else:
        # In --json mode we suppress live rendering. The stream is in
        # audit.jsonl; only the final outcome lands on stdout.
        bus.subscribe(RichRenderer(file=sys.stderr))

    try:
        outcome = run_audit(
            project_dir=project,
            ccbridge_dir=ccbridge_dir,
            bus=bus,
            run_uuid=str(uuid.uuid4()),
        )
    except LockBusyError as exc:
        _print_stderr(
            f"CCBridge audit already running on this project "
            f"(holder run_uuid={exc.holder.run_uuid})."
        )
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(_outcome_to_dict(outcome)))
    else:
        # Human banner already produced by RichRenderer via emits.
        # Add a final terse line for scriptable tail-grepping.
        click.echo(
            f"final_verdict={outcome.final_verdict} "
            f"run_uuid={outcome.run_uuid} "
            f"iterations={outcome.iterations_used}"
        )


def _outcome_to_dict(outcome: OrchestratorOutcome) -> dict[str, Any]:
    return {
        "run_uuid": outcome.run_uuid,
        "final_verdict": outcome.final_verdict,
        "iterations_used": outcome.iterations_used,
        "duration_sec": outcome.duration_sec,
    }


@audit.command("list")
@click.option(
    "--project",
    "project_opt",
    default=None,
    help="Project root. Defaults to git toplevel of cwd, or cwd.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON array of run summaries to stdout.",
)
def audit_list(project_opt: str | None, as_json: bool) -> None:
    """List all runs recorded in audit.jsonl, oldest first."""
    project = _resolve_project(project_opt)
    audit_path = project / CCBRIDGE_DIR_NAME / "audit.jsonl"
    summaries = _group_runs(_read_audit(audit_path))

    if as_json:
        click.echo(json.dumps([s.to_dict() for s in summaries]))
        return

    if not summaries:
        click.echo("(no runs yet)")
        return

    table = Table(title="CCBridge audit history")
    table.add_column("run_uuid", overflow="fold")
    table.add_column("verdict")
    table.add_column("iters", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("duration", justify="right")
    table.add_column("summary")

    for s in summaries:
        table.add_row(
            s.run_uuid,
            s.final_verdict or "?",
            str(s.iterations_used),
            f"${s.total_cost_usd:.2f}",
            f"{s.duration_sec:.1f}s",
            (s.last_summary or "")[:60],
        )

    Console().print(table)


@audit.command("get")
@click.argument("run_uuid", required=False)
@click.option(
    "--project",
    "project_opt",
    default=None,
    help="Project root. Defaults to git toplevel of cwd, or cwd.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit run details (summary + events) as JSON.",
)
def audit_get(
    run_uuid: str | None, project_opt: str | None, as_json: bool
) -> None:
    """Show details of one audit run. Omit RUN_UUID to get the last run."""
    project = _resolve_project(project_opt)
    audit_path = project / CCBRIDGE_DIR_NAME / "audit.jsonl"
    all_events = list(_read_audit(audit_path))

    target_uuid = run_uuid
    if target_uuid is None:
        if not all_events:
            _print_stderr("no audit history found")
            sys.exit(1)
        target_uuid = all_events[-1].run_uuid

    run_events = [e for e in all_events if e.run_uuid == target_uuid]
    if not run_events:
        _print_stderr(f"run_uuid {target_uuid!r} not found in audit log")
        sys.exit(1)

    summaries = _group_runs(run_events)
    summary = summaries[0]

    if as_json:
        payload = summary.to_dict()
        payload["events"] = [
            e.model_dump(mode="json") for e in run_events
        ]
        click.echo(json.dumps(payload))
        return

    console = Console()
    console.print(f"[bold]Run[/bold] {summary.run_uuid}")
    console.print(f"  Final verdict: {summary.final_verdict}")
    console.print(f"  Iterations:    {summary.iterations_used}")
    console.print(f"  Cost:          ${summary.total_cost_usd:.2f}")
    console.print(f"  Duration:      {summary.duration_sec:.1f}s")
    if summary.last_summary:
        console.print(f"  Summary:       {summary.last_summary}")
    console.print("\n[bold]Events[/bold]")
    renderer = RichRenderer()
    for e in run_events:
        renderer(e)


@audit.command("watch")
@click.option(
    "--project",
    "project_opt",
    default=None,
    help="Project root. Defaults to git toplevel of cwd, or cwd.",
)
@click.option(
    "--from-start",
    is_flag=True,
    default=False,
    help="Render existing audit.jsonl history before tailing.",
)
@click.option(
    "--poll-interval",
    "poll_interval_sec",
    default=0.5,
    type=float,
    help="Seconds between filesystem polls.",
)
@click.option(
    "--max-iterations",
    "max_iterations",
    default=None,
    type=int,
    help="Stop after this many polling cycles (mainly for tests).",
)
def audit_watch(
    project_opt: str | None,
    from_start: bool,
    poll_interval_sec: float,
    max_iterations: int | None,
) -> None:
    """Tail audit.jsonl into the terminal (run in a second window)."""
    project = _resolve_project(project_opt)
    audit_path = project / CCBRIDGE_DIR_NAME / "audit.jsonl"

    renderer = RichRenderer()  # stdout — second-terminal user
    try:
        watch_audit_log(
            audit_path=audit_path,
            renderer=renderer,
            poll_interval_sec=poll_interval_sec,
            from_start=from_start,
            max_iterations=max_iterations,
        )
    except KeyboardInterrupt:
        # Graceful Ctrl+C exit; no traceback to user.
        sys.exit(0)


# ---------------------------------------------------------------------------
# `status` (read-only minimal in 6a)
# ---------------------------------------------------------------------------


@cli.command("status")
@click.option(
    "--project",
    "project_opt",
    default=None,
    help="Project root. Defaults to git toplevel of cwd, or cwd.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON status object.",
)
def status(project_opt: str | None, as_json: bool) -> None:
    """Show minimal project status: last run, lockfile holder if any."""
    project = _resolve_project(project_opt)
    ccbridge_dir = project / CCBRIDGE_DIR_NAME
    audit_path = ccbridge_dir / "audit.jsonl"
    lockfile_path = ccbridge_dir / "lockfile"

    summaries = _group_runs(_read_audit(audit_path))
    last = summaries[-1].to_dict() if summaries else None

    locked = lockfile_path.exists()
    lock_run_uuid: str | None = None
    if locked:
        try:
            holder = LockHolder.from_json(
                lockfile_path.read_text(encoding="utf-8")
            )
            lock_run_uuid = holder.run_uuid
        except Exception:
            lock_run_uuid = None

    payload = {
        "project_dir": str(project),
        "ccbridge_dir": str(ccbridge_dir),
        "audit_log_exists": audit_path.exists(),
        "locked": locked,
        "lock_run_uuid": lock_run_uuid,
        "last_run": last,
    }

    if as_json:
        click.echo(json.dumps(payload))
        return

    console = Console()
    console.print(f"[bold]Project:[/bold] {project}")
    console.print(f"  ccbridge dir:    {ccbridge_dir}")
    console.print(
        f"  audit.jsonl:     "
        f"{'present' if audit_path.exists() else '(not yet created)'}"
    )
    if locked:
        console.print(f"  [yellow]locked[/yellow] by run {lock_run_uuid}")
    else:
        console.print("  [green]idle[/green]")
    if last is not None:
        console.print(
            f"  Last run:        {last['run_uuid']} → "
            f"{last['final_verdict']} ({last['iterations_used']} iter)"
        )
    else:
        console.print("  Last run:        (none yet)")


# ---------------------------------------------------------------------------
# `stop-hook` — Claude Code Stop hook entry point (wraps stop_hook_main)
# ---------------------------------------------------------------------------


@cli.command("stop-hook")
def stop_hook() -> None:
    """Process Claude Code Stop hook event (read JSON from stdin).

    This is the command Claude Code invokes via .claude/settings.json
    after each turn. It reads JSON from stdin, decides whether to
    block stopping (verdict=fail) or let Claude stop with a reason
    (needs_human/error/skipped/lock_busy), and writes decision JSON
    to stdout. Empty stdout means "no opinion".

    Fail-open contract: any internal error → empty stdout, diagnostic
    on stderr, exit 0. Never wedge a Claude session.

    See ``transports/stop_hook.py`` for the full contract and
    https://code.claude.com/docs/en/hooks for upstream semantics.
    """
    sys.exit(stop_hook_main())


# ---------------------------------------------------------------------------
# `init` / `uninstall` — declared but deferred to 6b
# ---------------------------------------------------------------------------


CONFIG_TEMPLATE = """# CCBridge project config (created by `ccbridge init`).
#
# Edit and commit to git so your team shares review settings.
# See ARCHITECTURE.md §2.6 for hard caps and §6.1 for secrets policy.

[project]
name = "untitled"

[review]
context_level = "medium"        # minimal | medium | full
max_iterations = 3              # hard cap before needs_human (AC-3)
max_diff_lines = 2000           # pre-flight (AC-14)
max_file_lines = 1500
max_total_tokens = 100000
include_rules = []              # list of paths to project Rulebook entries
include_recent_audits = 3
verdict_confidence_threshold = 0.7

[codex]
model = "gpt-4o"
api_key_env = "OPENAI_API_KEY"  # only the env var NAME, never the value

[claude]
api_key_env = "ANTHROPIC_API_KEY"
"""


CCBRIDGE_GITIGNORE = """# CCBridge runtime artefacts — never commit these.
# Generated by `ccbridge init`; safe to edit if you know what you're doing.
lockfile
state.json
state.json.tmp
audit.jsonl
audit.jsonl.tmp
identity.json
iteration-*/
rules-cache.sha256
"""


def _ccbridge_command_markers(subcommand: str) -> tuple[str, ...]:
    """Substrings that identify a CCBridge-installed hook command.

    We need to recognise BOTH:
      * the new absolute-path form: ``... -m ccbridge.cli stop-hook``
        (audit finding #3 fix, written by current ``init``)
      * the legacy bare form: ``ccbridge stop-hook``
        (written by pre-fix ``init`` versions; users upgrading from
        an earlier CCBridge install may have this in their settings.json)

    Without recognising legacy entries, ``init`` on a project that was
    initialized by an old version would skip the legacy entry as
    "not ours" and add a second one (duplication), and ``uninstall``
    would leave the legacy entry behind.

    NB: marker check is a substring match on the ``command`` field. We
    deliberately keep the substrings precise enough not to false-match
    a user's own hook that happens to mention "ccbridge" or "stop-hook"
    in passing.
    """
    return (
        f"ccbridge.cli {subcommand}",  # new (post-2026-05-03)
        f"ccbridge {subcommand}",      # legacy (pre-2026-05-03)
    )


def _stop_hook_command_marker() -> str:
    """Single substring for backward compatibility. Use
    :func:`_ccbridge_command_markers` for matching, this stays for
    callers that just want a sample marker (e.g. for log messages).
    """
    return "ccbridge.cli stop-hook"


def _quote_for_shell(path: str) -> str:
    """Wrap path in double quotes if it contains spaces.

    Claude Code parses hook ``command`` as a shell command. On Windows,
    Python venvs can sit under "Program Files" / "AppData/Local" with
    spaces — bare path would split into multiple argv tokens. Always
    quote when needed; never harm to over-quote a path without spaces
    but we keep it minimal for readability.
    """
    if " " in path:
        return f'"{path}"'
    return path


def _hook_command(subcommand: str) -> str:
    """Build the absolute hook command Claude Code will invoke.

    Audit finding #3 (2026-05-03): bare ``ccbridge stop-hook`` is a
    PATH-hijack risk + unstable across venvs. We instead point Claude
    at ``<sys.executable> -m ccbridge.cli <subcommand>``: the exact
    Python interpreter that ran ``ccbridge init``, so the hook always
    resolves to the same package version that was installed at
    init-time, regardless of subsequent PATH changes.
    """
    return f"{_quote_for_shell(sys.executable)} -m ccbridge.cli {subcommand}"


def _build_stop_hook_entry() -> dict[str, Any]:
    return {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": _hook_command("stop-hook"),
            }
        ],
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic JSON write via temp file + os.replace.

    Hatch'ed out as a module-level helper so the rollback test can
    monkeypatch it to simulate disk failure.
    """
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _patch_settings_json(
    settings_path: Path, *, force: bool
) -> tuple[bool, bool]:
    """Add a CCBridge Stop hook entry to settings.json.

    Returns (patched, backed_up):
      - patched: True if file was written (new or modified)
      - backed_up: True if a .ccbridge.bak was created during this call

    Raises OSError on atomic write failure. Caller is responsible for
    backup-restore on failure.

    Backup discipline (audit finding #1, 2026-05-03): backup is created
    ONLY when we are about to actually write. If the file already exists
    AND already contains our entry AND not force — return early WITHOUT
    creating a backup. Otherwise we'd overwrite a legitimate pre-CCBridge
    backup with the post-init state on every repeat init, which then
    poisons uninstall (it would restore that bogus backup and leave our
    hook installed). Backup must always reflect pre-CCBridge state.
    """
    existing: dict[str, Any] = {}
    file_existed = settings_path.exists()

    if file_existed:
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise OSError(
                f"existing {settings_path} is not valid JSON; "
                "refusing to patch"
            ) from None
        if not isinstance(existing, dict):
            raise OSError(
                f"existing {settings_path} top-level is not an object"
            )

    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise OSError(
            f"{settings_path} has hooks key but it's not an object"
        )
    stop = hooks.setdefault("Stop", [])
    if not isinstance(stop, list):
        raise OSError(
            f"{settings_path} hooks.Stop is not an array"
        )

    markers = _ccbridge_command_markers("stop-hook")
    already_present = any(
        _entry_matches_markers(entry, markers)
        for entry in stop
        if isinstance(entry, dict)
    )

    if already_present and not force:
        # Nothing to do, nothing to back up.
        return False, False

    # We ARE going to modify. Take backup now if the file pre-existed
    # AND we don't already have a backup (existing backup is more
    # authoritatively "pre-CCBridge" than what we're about to overwrite).
    backed_up = False
    if file_existed:
        backup = settings_path.with_suffix(
            settings_path.suffix + ".ccbridge.bak"
        )
        if not backup.exists():
            backup.write_text(
                settings_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            backed_up = True

    if force or already_present:
        # Strip any prior ccbridge entries — both new-form and legacy.
        # ``already_present`` here means we ARE force=True (because
        # without force we returned earlier), so this drops the old
        # entries before the clean append below.
        stop[:] = [
            entry
            for entry in stop
            if not isinstance(entry, dict)
            or not _entry_matches_markers(entry, markers)
        ]

    stop.append(_build_stop_hook_entry())
    _atomic_write_json(settings_path, existing)
    return True, backed_up


def _entry_matches_markers(
    entry: dict[str, Any], markers: tuple[str, ...]
) -> bool:
    """True if any hook in this Stop-array entry's hooks list contains
    one of our markers in its command. Tolerant of malformed shapes.
    """
    hooks = entry.get("hooks") or []
    if not isinstance(hooks, list):
        return False
    for h in hooks:
        if not isinstance(h, dict):
            continue
        cmd = h.get("command") or ""
        if any(marker in cmd for marker in markers):
            return True
    return False


def _restore_settings_backup(settings_path: Path) -> None:
    """Restore settings.json from .ccbridge.bak, if that backup exists."""
    backup = settings_path.with_suffix(
        settings_path.suffix + ".ccbridge.bak"
    )
    if backup.exists():
        settings_path.write_text(
            backup.read_text(encoding="utf-8"), encoding="utf-8"
        )


@cli.command("init")
@click.argument("path")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Recreate identity/config and re-add Stop hook entry even "
    "if already initialized.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON summary of what was done.",
)
def init(path: str, force: bool, as_json: bool) -> None:
    """Install CCBridge into a project: create .ccbridge/ + Stop hook entry."""
    from ccbridge.core.state import init_identity

    project = Path(path).resolve()
    project.mkdir(parents=True, exist_ok=True)
    ccbridge_dir = project / CCBRIDGE_DIR_NAME
    claude_dir = project / ".claude"
    settings_path = claude_dir / "settings.json"

    # 1. .ccbridge/ + identity (regenerate identity only on --force).
    ccbridge_dir.mkdir(parents=True, exist_ok=True)
    identity_path = ccbridge_dir / "identity.json"
    if force and identity_path.exists():
        identity_path.unlink()
    identity = init_identity(identity_path)

    # 2. config.toml — only if missing or --force.
    config_path = ccbridge_dir / "config.toml"
    if not config_path.exists() or force:
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

    # 3. .ccbridge/.gitignore — same.
    gitignore_path = ccbridge_dir / ".gitignore"
    if not gitignore_path.exists() or force:
        gitignore_path.write_text(CCBRIDGE_GITIGNORE, encoding="utf-8")

    # 4. Settings patch with backup + rollback.
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_pre_existed = settings_path.exists()
    try:
        patched, backed_up = _patch_settings_json(
            settings_path, force=force
        )
    except OSError as exc:
        # Rollback: if we already backed up, restore. .ccbridge/ stays
        # — it doesn't break Claude.
        if settings_pre_existed:
            _restore_settings_backup(settings_path)
        _print_stderr(
            f"ccbridge init: failed to patch {settings_path}: {exc}"
        )
        sys.exit(2)

    summary = {
        "project_dir": str(project),
        "ccbridge_dir": str(ccbridge_dir),
        "project_id": identity.project_id,
        "settings_patched": patched,
        "settings_backed_up": backed_up,
        "force": force,
    }

    if as_json:
        click.echo(json.dumps(summary))
        return

    console = Console()
    console.print(f"[bold green]✓[/bold green] CCBridge initialized at {project}")
    console.print(f"  project_id:   {identity.project_id}")
    console.print(f"  config:       {config_path}")
    console.print(
        f"  settings:     {'patched' if patched else 'unchanged (already configured)'}"
    )
    if backed_up:
        console.print(
            f"  backup:       {settings_path}.ccbridge.bak"
        )


def _is_ccbridge_entry(entry: Any) -> bool:
    """Predicate: True if this Stop-array entry is OUR ccbridge entry.

    Recognises both the new absolute-path form and legacy bare form.
    See :func:`_ccbridge_command_markers` for the marker set.
    """
    if not isinstance(entry, dict):
        return False
    return _entry_matches_markers(
        entry, _ccbridge_command_markers("stop-hook")
    )


def _unpatch_settings_json(settings_path: Path) -> bool:
    """Remove our Stop hook entry from settings.json.

    Returns True if the file was modified. Side effects:
    - Removes our entry from hooks.Stop
    - Drops empty hooks.Stop array
    - Drops empty hooks dict
    - If file becomes effectively empty AND a .ccbridge.bak exists,
      restores from backup; otherwise deletes settings.json (we
      created it, leave the workspace as we found it).
    """
    if not settings_path.exists():
        return False

    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Corrupted — don't touch.
        return False
    if not isinstance(existing, dict):
        return False

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False
    stop = hooks.get("Stop")
    if not isinstance(stop, list):
        return False

    before = len(stop)
    stop[:] = [entry for entry in stop if not _is_ccbridge_entry(entry)]
    after = len(stop)
    if before == after:
        return False  # nothing to remove

    if not stop:
        del hooks["Stop"]
    if not hooks:
        del existing["hooks"]

    backup = settings_path.with_suffix(
        settings_path.suffix + ".ccbridge.bak"
    )

    if not existing:
        # File would be empty after our cleanup. Two cases:
        if backup.exists():
            # User had pre-existing settings.json — restore the original.
            settings_path.write_text(
                backup.read_text(encoding="utf-8"), encoding="utf-8"
            )
            backup.unlink()
        else:
            # We created the file ourselves; remove it cleanly.
            settings_path.unlink()
    else:
        _atomic_write_json(settings_path, existing)
        # Backup is no longer relevant; remove if it exists.
        if backup.exists():
            backup.unlink()

    return True


@cli.command("uninstall")
@click.argument("path")
@click.option(
    "--yes",
    "yes_remove_data",
    is_flag=True,
    default=False,
    help="Confirm removal of .ccbridge/ (audit history). Required "
    "unless --keep-data is set.",
)
@click.option(
    "--keep-data",
    is_flag=True,
    default=False,
    help="Remove the Stop hook entry but keep .ccbridge/ on disk "
    "(audit history preserved).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON summary of what was done.",
)
def uninstall(
    path: str, yes_remove_data: bool, keep_data: bool, as_json: bool
) -> None:
    """Remove CCBridge from a project.

    Removes the ``ccbridge stop-hook`` entry from ``.claude/settings.json``
    (preserving any other hooks the user has). By default also removes
    ``.ccbridge/`` if you pass ``--yes``; use ``--keep-data`` to keep
    the audit history.
    """
    project = Path(path).resolve()

    if not yes_remove_data and not keep_data:
        _print_stderr(
            "ccbridge uninstall: refusing to proceed without --yes or "
            "--keep-data. .ccbridge/ contains audit history; pass "
            "--yes to delete it or --keep-data to retain it."
        )
        sys.exit(2)

    settings_path = project / ".claude" / "settings.json"
    settings_modified = _unpatch_settings_json(settings_path)

    ccbridge_dir = project / CCBRIDGE_DIR_NAME
    ccbridge_removed = False
    if not keep_data and ccbridge_dir.exists():
        import shutil

        shutil.rmtree(ccbridge_dir, ignore_errors=False)
        ccbridge_removed = True

    summary = {
        "project_dir": str(project),
        "settings_modified": settings_modified,
        "ccbridge_dir_removed": ccbridge_removed,
        "kept_data": keep_data,
    }

    if as_json:
        click.echo(json.dumps(summary))
        return

    console = Console()
    console.print(f"[bold]CCBridge uninstall[/bold] {project}")
    console.print(
        f"  settings:    {'modified' if settings_modified else 'no changes needed'}"
    )
    if keep_data:
        console.print(f"  data:        kept ({ccbridge_dir})")
    else:
        console.print(
            f"  data:        {'removed' if ccbridge_removed else 'nothing to remove'}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_audit(audit_path: Path) -> Iterable[CCBridgeEvent]:
    if not audit_path.exists():
        return []
    return AuditLog(audit_path).read_all()


def _print_stderr(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


__all__ = ("cli",)


if __name__ == "__main__":
    # Allows ``python -m ccbridge.cli`` invocation. Used by the Stop /
    # UserPromptSubmit hook commands written into .claude/settings.json
    # — those commands run via the project's Python interpreter so we
    # don't depend on a global PATH ``ccbridge`` shim.
    cli()
