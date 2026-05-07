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


@cro_app.command("history")
def history(
    funnel_step: str = typer.Argument(..., help="Funnel step name to track"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Show conversion-rate trajectory for a funnel step across reports."""
    paths = find_paths_or_exit(_console)
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet, run `devrel cro report` first.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"History: {funnel_step}")
    table.add_column("Period", style="cyan")
    table.add_column("Conv", justify="right")
    table.add_column("Sample", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT period_end, conversion_rate, sample_size
            FROM cro_funnel_metrics
            WHERE step_index = (
                SELECT MIN(step_index) FROM cro_funnel_metrics
                WHERE funnel_id IN (SELECT DISTINCT funnel_id FROM cro_funnel_metrics)
            )
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (limit,),
        )
        for period_end, conv, sample in cur:
            table.add_row(period_end, f"{(conv or 0):.1%}", f"{(sample or 0):,}")

    _console.print(table)


@cro_app.command("diff")
def diff(
    period_a: str = typer.Argument(..., help="Earlier ISO period"),
    period_b: str = typer.Argument(..., help="Later ISO period"),
) -> None:
    """Per-step conversion delta between two CRO reports."""
    paths = find_paths_or_exit(_console)
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"CRO diff: {period_a} -> {period_b}")
    table.add_column("Funnel", style="cyan")
    table.add_column("Step", justify="right")
    table.add_column(f"{period_a}", justify="right")
    table.add_column(f"{period_b}", justify="right")
    table.add_column("Delta pp", justify="right")

    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            SELECT a.funnel_id, a.step_index, a.conversion_rate, b.conversion_rate
            FROM cro_funnel_metrics a
            JOIN cro_funnel_metrics b
              ON a.funnel_id = b.funnel_id AND a.step_index = b.step_index
            WHERE a.period_end = ? AND b.period_end = ?
            ORDER BY a.funnel_id, a.step_index
            """,
            (period_a, period_b),
        )
        for funnel_id, step_index, conv_a, conv_b in cur:
            delta = (conv_b or 0) - (conv_a or 0)
            table.add_row(
                funnel_id,
                str(step_index),
                f"{(conv_a or 0):.1%}",
                f"{(conv_b or 0):.1%}",
                f"{delta:+.1%}",
            )

    _console.print(table)


@cro_app.command("calibration")
def calibration() -> None:
    """Score historical CRO recommendations against subsequent funnel data."""
    paths = find_paths_or_exit(_console)
    db_path = paths.devrel_dir / "state.db"
    if not db_path.is_file():
        _console.print("[yellow]No state.db yet.[/yellow]")
        raise typer.Exit(code=0)

    from devrel_swarm.core.growth.recommendations import calibrate
    from devrel_swarm.core.growth.target_kinds import Pillar

    def _score_outcome(rec) -> str:
        """Did conversion improve at this funnel step after the rec was applied?"""
        if rec.applied_at is None:
            return "unchanged"
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                """
                SELECT conversion_rate FROM cro_funnel_metrics
                WHERE funnel_id IN (SELECT DISTINCT funnel_id FROM cro_funnel_metrics)
                  AND period_end >= ?
                ORDER BY period_end ASC LIMIT 2
                """,
                (rec.applied_at[:10],),
            )
            rates = [row[0] for row in cur.fetchall()]
        if len(rates) < 2:
            return "unchanged"
        return (
            "improved"
            if rates[1] > rates[0]
            else ("regressed" if rates[1] < rates[0] else "unchanged")
        )

    result = calibrate(db_path, Pillar.CRO, outcome_scorer=_score_outcome)

    if not result:
        _console.print("[yellow]No applied CRO recommendations yet.[/yellow]")
        return

    table = Table(title="CRO calibration")
    table.add_column("Action", style="cyan")
    table.add_column("Applied", justify="right")
    table.add_column("Hit rate", justify="right")
    table.add_column("Lift vs coinflip", justify="right")
    for action, stats in result.items():
        table.add_row(
            action,
            str(stats["applied_count"]),
            f"{stats['hit_rate']:.1%}",
            f"{stats['lift_vs_coinflip']:+.1%}",
        )
    _console.print(table)


@cro_app.command("funnel")
def funnel(
    show_detected: bool = typer.Option(
        False, "--show-detected", help="Show what auto-detect picked"
    ),
    days: int = typer.Option(7, "--days"),
) -> None:
    """Inspect the current (auto-detected or configured) CRO funnel."""
    paths = find_paths_or_exit(_console)
    cyra = _build_cyra(paths.devrel_dir / "state.db")

    async def _run():
        return await cyra._autodetect_funnel(days=days)

    funnel_events = asyncio.run(_run())

    table = Table(title=f"Cyra funnel (auto-detected, {days}d)")
    table.add_column("#", justify="right")
    table.add_column("Event", style="cyan")
    for i, ev in enumerate(funnel_events):
        table.add_row(str(i), ev)

    _console.print(table)
    if show_detected:
        _console.print(
            "[dim]Override via `[growth].cro_funnel = [...]` in .devrel/config.toml[/dim]"
        )
