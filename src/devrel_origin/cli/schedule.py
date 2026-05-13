"""`devrel schedule {install, list, remove}` — wraps tools.scheduler.Scheduler.

The Scheduler ctor accepts `project_dir` (string path); we pass the
project root resolved from `.devrel/`. The CLI is a thin wrapper around
`install_cron`, `remove_cron`, and `list_entries`.
"""

from __future__ import annotations

import typer
from rich.console import Console

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.tools.scheduler import Scheduler

console = Console()

schedule_app = typer.Typer(
    name="schedule",
    help="Manage cron-based agent scheduling.",
    no_args_is_help=True,
    add_completion=False,
)


def _build_scheduler() -> Scheduler:
    paths = find_paths_or_exit(console)
    return Scheduler(project_dir=str(paths.root))


@schedule_app.command("install")
def install() -> None:
    """Install agent schedule into the user crontab."""
    sched = _build_scheduler()
    lines = sched.install_cron()
    console.print(f"[green]✓[/green] Installed {len(lines)} cron entry(ies)")
    for line in lines:
        console.print(f"  [dim]{line}[/dim]")


@schedule_app.command("list")
def list_entries() -> None:
    """List the configured schedule entries."""
    sched = _build_scheduler()
    entries = sched.list_entries()
    if not entries:
        console.print("[yellow]No schedule entries configured.[/yellow]")
        return
    for e in entries:
        flag = "[green]on[/green]" if e.get("enabled") else "[dim]off[/dim]"
        console.print(
            f"  {flag}  [bold]{e.get('name', '?')}[/bold]  "
            f"[dim]{e.get('cron', '?')}[/dim]  {e.get('description', '')}"
        )


@schedule_app.command("remove")
def remove() -> None:
    """Remove all devrel-origin entries from the user crontab."""
    sched = _build_scheduler()
    sched.remove_cron()
    console.print("[green]✓[/green] Removed devrel-origin cron entries")
