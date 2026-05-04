"""`devrel run` — full weekly pipeline, health check, or single agent."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit

console = Console()


def run_command(
    health: bool = typer.Option(False, "--health", help="Only run the Watchdog health check."),
    agent: str = typer.Option("", "--agent", help="Run a single agent by name (e.g., 'kai')."),
    task: str = typer.Option("", "--task", help="Task description for --agent."),
) -> None:
    """Run the full weekly pipeline (default), or a subset via flags."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        if health:
            result = await atlas.run_single_task("watchdog", "Check system health")
            console.print(
                f"[green]✓[/green] watchdog: {str(result.result)[:300]}"
                if result.success
                else f"[red]✗[/red] {result.error}"
            )
            return
        if agent:
            t = task or f"Run {agent} with default settings"
            result = await atlas.run_single_task(agent, t)
            if result.success:
                console.print(f"[green]✓[/green] {agent}: {str(result.result)[:300]}")
            else:
                console.print(f"[red]✗[/red] {agent} failed: {result.error}")
                raise typer.Exit(code=1)
            return
        # Full weekly pipeline.
        ctx = await atlas.run_weekly_cycle()
        console.print(f"[bold green]Weekly cycle complete.[/bold green] week_of={ctx.week_of}")

    asyncio.run(_do())
