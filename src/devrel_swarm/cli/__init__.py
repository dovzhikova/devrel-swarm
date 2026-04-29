"""Typer CLI app for devrel-swarm.

Phase 2 registers `init` and `doctor`. Later phases register additional
verb groups (run, content, sales, marketing, etc.).
"""

from __future__ import annotations

import typer

from devrel_swarm import __version__
from devrel_swarm.cli.init import init_command


app = typer.Typer(
    name="devrel",
    help="DevRel + Sales + Marketing agent system. Run from inside a project repo.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"devrel-swarm {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Root callback. Subcommands are registered below."""
    return None


app.command(name="init")(init_command)


if __name__ == "__main__":
    app()
