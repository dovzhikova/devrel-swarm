"""`devrel cro ...`: CRO auditor verbs (Cyra)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.core.cyra import Cyra
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient

cro_app = typer.Typer(
    name="cro",
    help="CRO auditor (Cyra). Funnel drop-offs + LLM-generated A/B hypotheses.",
    no_args_is_help=True,
)

_console = Console()


def _build_cyra(db_path: Path) -> Cyra:
    """Construct Cyra with clients from environment variables. Patched in unit tests."""
    posthog = PostHogClient(
        api_key=os.environ.get("POSTHOG_API_KEY", ""),
        project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
    )
    llm = LLMClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    llm.set_agent("cyra")
    return Cyra(
        posthog_client=posthog,
        llm_client=llm,
        db_path=db_path,
    )


@cro_app.command("report")
def report(
    since: str = typer.Option("7d", "--since", help="Window: 7d, 30d, 90d"),
    push: bool = typer.Option(False, "--push", help="Email/Telegram the report"),
    format: str = typer.Option("markdown", "--format", help="markdown|json"),
) -> None:
    """Run a Cyra cycle and persist Recommendation rows + Mox briefs."""
    paths = find_paths_or_exit(_console)
    _days = int(since.rstrip("d"))
    period_end = date.today().isoformat()

    # Insert an analytics_reports row to anchor the FK from analytics_recommendations.
    # The CLI is the report producer here; Cyra emits recommendation rows attached
    # to this report_id.
    db_path = paths.state_db
    period_start = (date.today() - timedelta(days=_days)).isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            (period_start, period_end, "{}"),
        )
        report_id = cur.lastrowid
        conn.commit()

    cyra = _build_cyra(db_path)

    async def _run():
        return await cyra.execute(
            period_end=period_end,
            report_id=report_id,
            page_html_by_url={},
            iris_themes=[],
            sage_friction=[],
            deliverables_dir=paths.deliverables_dir,
        )

    result = asyncio.run(_run())

    if format == "json":
        _console.print(
            json.dumps(
                {
                    "period_end": result.period_end,
                    "funnel_id": result.funnel_id,
                    "dropoffs": [
                        {
                            "from_step": d.from_step,
                            "to_step": d.to_step,
                            "conversion_rate": d.conversion_rate,
                            "pp_delta_vs_prior": d.pp_delta_vs_prior,
                            "sample_size": d.sample_size,
                        }
                        for d in result.dropoffs
                    ],
                    "recommendations": [
                        {
                            "action": r.action,
                            "target": r.target,
                            "confidence": r.confidence,
                            "source_ids": r.source_ids,
                        }
                        for r in result.recommendations
                    ],
                },
                indent=2,
                default=str,
            )
        )
        return

    # Markdown table via Rich
    table = Table(title=f"Cyra report: {period_end}")
    table.add_column("From -> To", style="cyan")
    table.add_column("Conv", justify="right")
    table.add_column("WoW Delta", justify="right")
    table.add_column("Sample", justify="right")
    for d in result.dropoffs:
        table.add_row(
            f"{d.from_step} -> {d.to_step}",
            f"{d.conversion_rate:.1%}",
            f"{d.pp_delta_vs_prior:+.1%}",
            f"{d.sample_size:,}",
        )
    _console.print(table)
    _console.print(f"[green]Wrote {len(result.recommendations)} recommendation(s).[/green]")
    if push:
        _console.print("[yellow]--push not yet implemented for cro; printed-only.[/yellow]")
