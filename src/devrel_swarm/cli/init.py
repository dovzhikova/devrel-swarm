"""`devrel init` command — bootstrap .devrel/ in cwd."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from devrel_swarm.project.init import InitOptions, init_project

console = Console()


def init_command(
    name: str = typer.Option(
        ...,
        "--name",
        prompt="Project name (e.g., 'openclaw')",
        help="The product this devrel-swarm instance covers.",
    ),
    url: str = typer.Option(
        "",
        "--url",
        prompt="Project URL (or empty)",
        help="Public homepage URL for the product. Optional.",
    ),
    github_repo: str = typer.Option(
        "",
        "--github-repo",
        prompt="GitHub repo as 'owner/name' (or empty)",
        help="Optional. Used by Sage for issue triage.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be created without writing anything.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Skip prompts. Requires --name (others default to empty/null).",
    ),
) -> None:
    """Bootstrap a `.devrel/` scaffold in the current directory."""
    if non_interactive and not name:
        console.print("[red]--non-interactive requires --name.[/red]")
        raise typer.Exit(code=2)

    opts = InitOptions(
        name=name,
        url=url,
        github_repo=github_repo or None,
        dry_run=dry_run,
    )
    result = init_project(Path.cwd(), opts)

    if result.dry_run:
        console.print("[yellow]Dry run — nothing written.[/yellow]")
        for entry in result.would_create:
            console.print(f"  + {entry}")
        return

    for entry in result.created:
        console.print(f"  [green]+[/green] {entry}")
    for entry in result.skipped:
        console.print(f"  [dim]= {entry} (existed; preserved)[/dim]")
    console.print()
    console.print("[bold green]Done.[/bold green] Next steps:")
    console.print(
        "  1. Run [cyan]devrel auth[/cyan] to configure your LLM API key (Anthropic or OpenRouter)."
    )
    console.print(
        "  2. Edit voice.md / style.md / slop-blocklist.md to match your project's voice."
    )
    console.print("  3. Run [cyan]devrel doctor[/cyan] to verify everything is wired up.")
    console.print(
        "[dim]Tip: OpenRouter offers free monthly credits and supports per-agent model "
        "routing. Sign up at https://openrouter.ai/.[/dim]"
    )
