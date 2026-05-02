"""RichRenderer — terminal output for the CCBridge event stream.

Used as a Stop-hook stdout renderer (and any time we want pretty CLI
output for an audit run). Format is keyed off the event type and uses
``rich`` for colour/structure when a tty is attached, plain text
otherwise.

Per ADR-002 this is broadcast-only: it does NOT touch ``audit.jsonl``.

We avoid ``rich.live`` for the v0.1 scope — orchestrator emits at
human-readable cadence (started → context_built → codex_thinking →
verdict), so a sequential pretty print is sufficient. Live spinners
move into v0.2 when long-running Codex calls warrant a progress UI.
"""

from __future__ import annotations

from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ccbridge.core.events import (
    CCBridgeEvent,
    CodexThinkingEvent,
    ContextBuiltEvent,
    ErrorEvent,
    IterationCompleteEvent,
    StartedEvent,
    VerdictEvent,
    WarningEvent,
)

_VERDICT_STYLE: dict[str, str] = {
    "pass": "bold green",
    "fail": "bold red",
    "needs_human": "bold yellow",
    "skipped": "dim",
    "error": "bold red on white",
}


class RichRenderer:
    """Pretty-print events to a :class:`rich.console.Console`.

    Conforms to :class:`Renderer` Protocol. ``console`` is injectable
    for tests and for callers who want to redirect output (e.g. to a
    file or a stderr stream during a Stop hook).
    """

    def __init__(
        self,
        console: Console | None = None,
        file: TextIO | None = None,
    ) -> None:
        if console is not None:
            self.console = console
        else:
            # ``file=None`` → stdout, which is what capsys captures.
            self.console = Console(file=file, force_terminal=False)

    def __call__(self, event: CCBridgeEvent) -> None:
        if isinstance(event, StartedEvent):
            self._render_started(event)
        elif isinstance(event, ContextBuiltEvent):
            self._render_context_built(event)
        elif isinstance(event, CodexThinkingEvent):
            self._render_codex_thinking(event)
        elif isinstance(event, VerdictEvent):
            self._render_verdict(event)
        elif isinstance(event, IterationCompleteEvent):
            self._render_iteration_complete(event)
        elif isinstance(event, ErrorEvent):
            self._render_error(event)
        elif isinstance(event, WarningEvent):
            self._render_warning(event)
        else:
            self._render_generic(event)

    # ------------------------------------------------------------------
    # Per-event renderers
    # ------------------------------------------------------------------

    def _render_started(self, event: StartedEvent) -> None:
        self.console.print(
            f"[bold cyan]✻ ccbridge audit[/bold cyan] "
            f"(run_uuid={event.run_uuid})"
        )
        self.console.print(
            f"  Project: {event.project_name} "
            f"({event.iteration_count}/{event.max_iterations})"
        )

    def _render_context_built(self, event: ContextBuiltEvent) -> None:
        cache = "✓ hit" if event.cache_hit else "✗ miss"
        self.console.print(
            f"  Context: [bold]{event.diff_lines}[/bold] diff lines, "
            f"{event.files_count} files, "
            f"{event.rules_count} rules ([dim]cache {cache}[/dim])"
        )

    def _render_codex_thinking(self, event: CodexThinkingEvent) -> None:
        eta = (
            f" (~{event.eta_seconds}s)" if event.eta_seconds else ""
        )
        self.console.print(f"  [yellow]Codex reviewing...[/yellow]{eta}")

    def _render_verdict(self, event: VerdictEvent) -> None:
        style = _VERDICT_STYLE.get(event.verdict, "bold")
        issues = event.issues
        sev_line = (
            f"critical={issues.critical} major={issues.major} "
            f"minor={issues.minor} info={issues.info}"
        )
        body = Text()
        body.append("Verdict: ", style="bold")
        body.append(event.verdict, style=style)
        body.append("\n")
        body.append(
            f"Confidence: {event.verdict_confidence:.2f} | "
            f"Completeness: {event.issues_completeness:.2f}\n"
        )
        body.append(
            f"Cost: ${event.cost_usd:.2f} | "
            f"Duration: {event.duration_sec:.1f}s\n"
        )
        body.append(f"Issues: {sev_line}\n\n")
        body.append(event.summary)

        self.console.print(Panel(body, border_style=style))

    def _render_iteration_complete(
        self, event: IterationCompleteEvent
    ) -> None:
        style = _VERDICT_STYLE.get(event.final_verdict, "bold")
        self.console.print(
            f"[{style}]→ Final: {event.final_verdict}[/{style}] "
            f"({event.iterations_used} iterations, "
            f"${event.total_cost_usd:.2f}, "
            f"{event.duration_sec:.1f}s)"
        )

    def _render_error(self, event: ErrorEvent) -> None:
        self.console.print(
            f"[bold red]ERROR[/bold red] [{event.error_type}] {event.message}"
        )

    def _render_warning(self, event: WarningEvent) -> None:
        self.console.print(
            f"[yellow]WARNING[/yellow] {event.message}"
        )
        if event.context:
            self.console.print(f"  context: {event.context}")

    def _render_generic(self, event: CCBridgeEvent) -> None:
        """Fallback for future event types we don't have explicit
        handlers for. Prints the event_type so debug isn't blind, but
        does NOT raise.
        """
        self.console.print(
            f"[dim]· event[{event.event_type}] run_uuid={event.run_uuid}[/dim]"
        )


__all__ = ("RichRenderer",)
