"""`devrel listen` — social-media listening via Echo."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def listen_command(
    platforms: str = typer.Option(
        "reddit,hn,twitter", "--platforms",
        help="Comma-separated platforms to scan.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Scan social media for product mentions and sentiment."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "echo", f"Scan {platforms} for product mentions"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
