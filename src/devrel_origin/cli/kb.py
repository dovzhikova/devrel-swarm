"""`devrel kb {add, list, refresh}` — wraps tools.kb_harvester.KBHarvester.

Wraps the existing `KBHarvester` tool. The harvester ctor takes
`kb_path: Path` plus an optional `firecrawl_api_key`; we pull the latter
from the environment so the CLI degrades gracefully when no key is set.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.tools.kb_harvester import KBHarvester

console = Console()

kb_app = typer.Typer(
    name="kb",
    help="Knowledge base: add URLs, list documents, refresh from configured sources.",
    no_args_is_help=True,
    add_completion=False,
)


def _build_harvester(kb_path: Path) -> KBHarvester:
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    return KBHarvester(kb_path=kb_path, firecrawl_api_key=api_key)


@kb_app.command("add")
def add(
    url: str = typer.Argument(..., help="URL to harvest into the knowledge base."),
    category: str = typer.Option(
        "misc", "--category", help="KB category folder for the harvested doc."
    ),
) -> None:
    """Harvest a single URL into the project KB."""
    paths = find_paths_or_exit(console)
    harvester = _build_harvester(paths.kb_dir)

    async def _do() -> None:
        try:
            doc = await harvester.harvest_url(url, category=category)
        finally:
            await harvester.close()
        if doc is None:
            console.print(f"[red]Failed to harvest {url}[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]✓[/green] Saved {doc.filename} → {doc.category}/")

    asyncio.run(_do())


@kb_app.command("list")
def list_docs() -> None:
    """List markdown docs currently in the KB."""
    paths = find_paths_or_exit(console)
    if not paths.kb_dir.exists():
        console.print("[yellow]No KB directory yet.[/yellow]")
        return
    docs = sorted(paths.kb_dir.rglob("*.md"))
    if not docs:
        console.print("[yellow]KB is empty.[/yellow]")
        return
    for d in docs:
        rel = d.relative_to(paths.kb_dir)
        console.print(f"  [dim]{rel}[/dim]")
    console.print(f"\n[green]{len(docs)} doc(s)[/green]")


@kb_app.command("refresh")
def refresh() -> None:
    """Re-harvest all configured KB sources."""
    paths = find_paths_or_exit(console)
    harvester = _build_harvester(paths.kb_dir)

    async def _do() -> None:
        try:
            report = await harvester.harvest_all()
        finally:
            await harvester.close()
        ok = report.get("harvested", 0)
        failed = report.get("failed", 0)
        console.print(f"[green]✓[/green] harvested={ok} failed={failed}")
        for src in report.get("sources", []):
            status = src.get("status", "?")
            name = src.get("name", "?")
            color = "green" if status == "ok" else "red"
            console.print(f"  [{color}]{status}[/{color}] {name}")

    asyncio.run(_do())
