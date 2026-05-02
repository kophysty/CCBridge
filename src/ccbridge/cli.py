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

import click
from rich.console import Console
from rich.table import Table

from ccbridge.core.audit_log import AuditLog
from ccbridge.core.event_bus import EventBus
from ccbridge.core.events import (
    CCBridgeEvent,
    IterationCompleteEvent,
    VerdictEvent,
)
from ccbridge.core.lockfile import LockBusyError, LockHolder
from ccbridge.core.orchestrator import OrchestratorOutcome, run_audit
from ccbridge.renderers.rich_renderer import RichRenderer
from ccbridge.transports.audit_watch import watch_audit_log

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
# `init` / `uninstall` — declared but deferred to 6b
# ---------------------------------------------------------------------------


@cli.command("init")
@click.argument("path")
def init(path: str) -> None:
    """[6b] Install CCBridge into a project (.ccbridge dir + Stop hook)."""
    _print_stderr(
        "ccbridge init is not implemented yet — deferred to PR2b step 6b "
        "(needs careful .claude/settings.json merge with backup/rollback). "
        "For now, manually create .ccbridge/ in your project and configure "
        "the Stop hook by hand."
    )
    sys.exit(2)


@cli.command("uninstall")
@click.argument("path")
def uninstall(path: str) -> None:
    """[6b] Remove CCBridge from a project."""
    _print_stderr(
        "ccbridge uninstall is not implemented yet — deferred to PR2b "
        "step 6b (paired with init: needs to restore .claude/settings.json "
        "from backup)."
    )
    sys.exit(2)


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
