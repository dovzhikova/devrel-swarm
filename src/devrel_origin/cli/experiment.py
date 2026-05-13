"""`devrel experiment` — A/B experiment design via Nova."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_origin.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()


def experiment_command(
    hypothesis: str = typer.Argument(..., help="The hypothesis to test."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Design an A/B experiment with power analysis via Nova."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task(
            "nova", f"Design experiment for hypothesis: {hypothesis}"
        )
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
