"""`devrel intel` — competitive intelligence via Rex."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def intel_command(
    competitor: str = typer.Argument(..., help="Competitor name."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Gather competitive intel on a named competitor."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "rex", f"Compile competitive intel on {competitor}"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
