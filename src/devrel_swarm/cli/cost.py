"""`devrel cost` — read the costs ledger from .devrel/state.db.

Reads the SQLite `costs` table populated by the LLM cost sink (Phase 4
Task 1). Reports total spend in USD, plus a per-agent breakdown. No
ANTHROPIC_API_KEY required — this only reads local state.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.project.state import open_db

console = Console()


def cost_command(
    month: str = typer.Option(
        "",
        "--month",
        help="Filter to a YYYY-MM slice (e.g., '2026-04').",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show recorded LLM cost totals from the project state DB."""
    paths = find_paths_or_exit(console)
    if not paths.state_db.is_file():
        console.print("[yellow]No state.db yet. Run an agent first.[/yellow]")
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "total_usd": 0.0,
                        "by_agent": {},
                        "calls": 0,
                        "month_filter": month or None,
                    }
                )
            )
        return

    # Build a parameterised WHERE clause. The `where` literal is one of two
    # fixed strings we control — user input flows only through `params`,
    # so SQL injection is impossible.
    where = ""
    params: tuple = ()
    if month:
        where = "WHERE recorded_at LIKE ?"
        params = (f"{month}%",)

    with open_db(paths.state_db) as conn:
        total_row = conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) AS total, COUNT(*) AS calls FROM costs {where}",
            params,
        ).fetchone()
        total_usd = float(total_row["total"]) if total_row else 0.0
        calls = int(total_row["calls"]) if total_row else 0

        by_agent_rows = conn.execute(
            f"SELECT agent, "
            f"COALESCE(SUM(cost_usd), 0.0) AS usd, "
            f"COALESCE(SUM(input_tokens), 0) AS in_tok, "
            f"COALESCE(SUM(output_tokens), 0) AS out_tok, "
            f"COUNT(*) AS calls "
            f"FROM costs {where} GROUP BY agent ORDER BY usd DESC",
            params,
        ).fetchall()

    by_agent = {
        r["agent"]: {
            "usd": float(r["usd"]),
            "input_tokens": int(r["in_tok"]),
            "output_tokens": int(r["out_tok"]),
            "calls": int(r["calls"]),
        }
        for r in by_agent_rows
    }

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "total_usd": total_usd,
                    "calls": calls,
                    "by_agent": by_agent,
                    "month_filter": month or None,
                },
                indent=2,
            )
        )
        return

    suffix = f" for {month}" if month else ""
    console.print(f"[bold]Total{suffix}:[/bold] ${total_usd:.4f}  [dim]({calls} call(s))[/dim]")
    if not by_agent:
        console.print("[dim]No cost rows yet.[/dim]")
        return
    console.print("\n[bold]By agent:[/bold]")
    for agent, row in by_agent.items():
        console.print(
            f"  {agent:>10s}  ${row['usd']:.4f}  "
            f"[dim]in={row['input_tokens']} out={row['output_tokens']} "
            f"calls={row['calls']}[/dim]"
        )
