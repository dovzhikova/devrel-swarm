"""`devrel sales {outreach, battlecard, sequence}` — Pax-powered sales surfaces."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from devrel_origin.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

sales_app = typer.Typer(
    name="sales",
    help="Sales enablement: outreach, battle cards, nurture sequences.",
    no_args_is_help=True,
    add_completion=False,
)


def _run(task: str, json_output: bool) -> None:
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("pax", task)
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())


@sales_app.command("outreach")
def outreach(
    company: str = typer.Argument(..., help="Target company."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Draft a cold outreach email for a target company."""
    _run(f"Draft outreach email for {company}", json_output)


@sales_app.command("battlecard")
def battlecard(
    competitor: str = typer.Argument(..., help="Competitor."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build a sales battle card against a competitor."""
    _run(f"Build battle card vs. {competitor}", json_output)


@sales_app.command("sequence")
def sequence(
    campaign: str = typer.Argument(..., help="Campaign description."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Design a multi-touch nurture sequence."""
    _run(f"Design nurture sequence: {campaign}", json_output)
