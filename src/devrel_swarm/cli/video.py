"""`devrel video record` — screen-recorded tutorials via Vox."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

video_app = typer.Typer(
    name="video",
    help="Video tutorial production.",
    no_args_is_help=True,
    add_completion=False,
)


@video_app.command("record")
def record(
    script: str = typer.Argument(..., help="Path to script markdown OR raw task description."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Record a screen-recorded video tutorial via Vox."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("vox", f"Record video tutorial: {script}")
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
