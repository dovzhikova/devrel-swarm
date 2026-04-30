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
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show recorded LLM cost totals from the project state DB."""
    paths = find_paths_or_exit(console)
    if not paths.state_db.is_file():
        console.print("[yellow]No state.db yet. Run an agent first.[/yellow]")
        if json_output:
            typer.echo(json.dumps({"total_usd": 0.0, "by_agent": {}, "calls": 0}))
        return

    with open_db(paths.state_db) as conn:
        total_row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total, COUNT(*) AS calls FROM costs"
        ).fetchone()
        total_usd = float(total_row["total"]) if total_row else 0.0
        calls = int(total_row["calls"]) if total_row else 0

        by_agent_rows = conn.execute(
            "SELECT agent, "
            "COALESCE(SUM(cost_usd), 0.0) AS usd, "
            "COALESCE(SUM(input_tokens), 0) AS in_tok, "
            "COALESCE(SUM(output_tokens), 0) AS out_tok, "
            "COUNT(*) AS calls "
            "FROM costs GROUP BY agent ORDER BY usd DESC"
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
                {"total_usd": total_usd, "calls": calls, "by_agent": by_agent},
                indent=2,
            )
        )
        return

    console.print(f"[bold]Total:[/bold] ${total_usd:.4f}  [dim]({calls} call(s))[/dim]")
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
