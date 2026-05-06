"""Cross-pillar `devrel growth` umbrella.

`summary` rolls up the latest report from each pillar (argus + cyra +
vega + selene) into a single Markdown table. `diff` shows pillar-level
movement between two periods. Pillar-specific verbs live in
`cli/{seo,geo,cro,argus}.py`.
"""

from __future__ import annotations

import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.core.growth.target_kinds import Pillar

growth_app = typer.Typer(
    name="growth",
    help="Cross-pillar Growth dashboard. Pillar-specific verbs live in `seo`, `geo`, `cro`, `argus`.",
    no_args_is_help=True,
)

_console = Console()


@growth_app.command("summary")
def summary(
    period: str = typer.Option("", "--period", help="ISO period (default: latest)"),
) -> None:
    """One-line-per-pillar status of the most recent report."""
    paths = find_paths_or_exit(_console)
    db_path = paths.state_db
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet. Run `devrel run` first.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title="Growth: pillar summary")
    table.add_column("Pillar", style="cyan")
    table.add_column("Open recs")
    table.add_column("Latest period")

    with sqlite3.connect(db_path) as conn:
        for pillar in Pillar:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS open_recs, MAX(first_seen_period) AS latest
                FROM analytics_recommendations
                WHERE pillar = ? AND applied_at IS NULL
                """,
                (pillar.value,),
            )
            row = cur.fetchone()
            open_recs = row[0] or 0
            latest = row[1] or "-"
            table.add_row(pillar.value, str(open_recs), latest)

    _console.print(table)


@growth_app.command("diff")
def diff(
    period_a: str = typer.Argument(..., help="Earlier ISO period"),
    period_b: str = typer.Argument(..., help="Later ISO period"),
) -> None:
    """Per-pillar count of new/closed recommendations between two periods."""
    paths = find_paths_or_exit(_console)
    db_path = paths.state_db
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet. Run `devrel run` first.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"Growth diff: {period_a} to {period_b}")
    table.add_column("Pillar", style="cyan")
    table.add_column("New", style="green")
    table.add_column("Closed", style="dim")

    with sqlite3.connect(db_path) as conn:
        for pillar in Pillar:
            cur = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN first_seen_period > ? AND first_seen_period <= ? THEN 1 ELSE 0 END) AS new_count,
                    SUM(CASE WHEN applied_at IS NOT NULL
                              AND date(applied_at) > ? AND date(applied_at) <= ?
                          THEN 1 ELSE 0 END) AS closed_count
                FROM analytics_recommendations
                WHERE pillar = ?
                """,
                (period_a, period_b, period_a, period_b, pillar.value),
            )
            row = cur.fetchone()
            table.add_row(pillar.value, str(row[0] or 0), str(row[1] or 0))

    _console.print(table)
