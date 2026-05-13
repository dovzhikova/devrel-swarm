"""`devrel marketing {blog, landing, social, campaign}` — Mox-powered surfaces."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_origin.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

marketing_app = typer.Typer(
    name="marketing",
    help="Marketing campaigns: blog posts, landing pages, social, full campaigns.",
    no_args_is_help=True,
    add_completion=False,
)


def _run(task: str, json_output: bool) -> None:
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("mox", task)
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())


@marketing_app.command("blog")
def blog(
    topic: str = typer.Argument(..., help="Blog topic."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Write a blog post on a topic."""
    _run(f"Write blog post: {topic}", json_output)


@marketing_app.command("landing")
def landing(
    topic: str = typer.Argument(..., help="Landing page topic."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Write landing page copy."""
    _run(f"Write landing page copy: {topic}", json_output)


@marketing_app.command("social")
def social(
    topic: str = typer.Argument(..., help="Social topic."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Write a social media batch."""
    _run(f"Write social batch: {topic}", json_output)


@marketing_app.command("campaign")
def campaign(
    brief: str = typer.Argument(..., help="Campaign brief."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build a full marketing campaign."""
    _run(f"Build campaign: {brief}", json_output)
