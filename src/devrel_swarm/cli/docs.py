"""`devrel docs build` — AST-based docs via Dex."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_swarm.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

docs_app = typer.Typer(
    name="docs",
    help="Documentation generation.",
    no_args_is_help=True,
    add_completion=False,
)


@docs_app.command("build")
def build(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build architecture docs + API reference from source via Dex."""
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("dex", "Build architecture docs and API reference")
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
