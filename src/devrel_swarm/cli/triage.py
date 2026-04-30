"""`devrel triage` — GitHub issue triage via Sage."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def triage_command(
    days: int = typer.Option(7, "--days", help="Look back this many days."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Triage GitHub issues from the last N days."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "sage", f"Triage GitHub issues from the last {days} days"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
