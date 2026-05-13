"""`devrel deliverables {list, show}` — browse generated artifacts.

Lists / cats files under .devrel/deliverables/ — the canonical output
directory used by `devrel content draft|audit` and the agent pipeline.
"""

from __future__ import annotations

import typer
from rich.console import Console

from devrel_origin.cli._common import find_paths_or_exit

console = Console()

deliverables_app = typer.Typer(
    name="deliverables",
    help="List and inspect generated content/artifacts under .devrel/deliverables/.",
    no_args_is_help=True,
    add_completion=False,
)


@deliverables_app.command("list")
def list_files() -> None:
    """List all deliverable files (newest first)."""
    paths = find_paths_or_exit(console)
    if not paths.deliverables_dir.exists():
        console.print("[yellow]No deliverables directory yet.[/yellow]")
        return
    files = sorted(
        paths.deliverables_dir.rglob("*"),
        key=lambda p: p.stat().st_mtime if p.is_file() else 0,
        reverse=True,
    )
    files = [p for p in files if p.is_file()]
    if not files:
        console.print("[yellow]No deliverables yet.[/yellow]")
        return
    for p in files:
        rel = p.relative_to(paths.deliverables_dir)
        size = p.stat().st_size
        console.print(f"  [dim]{size:>7d}[/dim]  {rel}")
    console.print(f"\n[green]{len(files)} file(s)[/green]")


@deliverables_app.command("show")
def show(
    name: str = typer.Argument(..., help="Filename (or substring) to display."),
) -> None:
    """Print the contents of a deliverable file (substring match on name)."""
    paths = find_paths_or_exit(console)
    if not paths.deliverables_dir.exists():
        console.print("[yellow]No deliverables directory yet.[/yellow]")
        raise typer.Exit(code=1)
    matches = [p for p in paths.deliverables_dir.rglob("*") if p.is_file() and name in p.name]
    if not matches:
        console.print(f"[red]No deliverable matching '{name}'[/red]")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        console.print(f"[yellow]Multiple matches for '{name}':[/yellow]")
        for p in matches:
            console.print(f"  {p.relative_to(paths.deliverables_dir)}")
        raise typer.Exit(code=1)
    typer.echo(matches[0].read_text())
