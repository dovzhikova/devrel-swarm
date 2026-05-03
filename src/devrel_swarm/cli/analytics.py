"""`devrel analytics report` — Argus performance report.

Pulls the last N days of metrics from PostHog, GitHub, Instantly, and
Echo's social_mentions; ranks deterministically; emits structured
recommendations via a single Sonnet call.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.core.argus import Argus, PerformanceReport

console = Console()
err_console = Console(stderr=True)

analytics_app = typer.Typer(
    name="analytics",
    help="Content performance analysis (Argus).",
    no_args_is_help=True,
)


_SINCE_RE = re.compile(r"^(\d+)([dwmy])$")


def _parse_since(since: str) -> timedelta:
    """Accept '7d' / '30d' / '12w' / '3m' / '1y'."""
    m = _SINCE_RE.match(since.strip())
    if not m:
        raise typer.BadParameter(
            f"--since must look like '7d', '30d', '12w': got {since!r}"
        )
    n, unit = int(m.group(1)), m.group(2)
    days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    return timedelta(days=n * days)


def _build_argus(state_db_path: Path) -> Argus:
    """Construct Argus with real collectors. Patched in unit tests."""
    import os

    from devrel_swarm.core.llm import LLMClient
    from devrel_swarm.tools.analytics import (
        GitHubCollector,
        InstantlyCollector,
        PostHogCollector,
        SocialCollector,
    )
    from devrel_swarm.tools.api_client import PostHogClient
    from devrel_swarm.tools.github_tools import GitHubTools
    from devrel_swarm.tools.instantly_client import InstantlyClient

    posthog_client = PostHogClient(
        api_key=os.environ.get("POSTHOG_API_KEY", ""),
        project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
    )
    github_client = GitHubTools(token=os.environ.get("GITHUB_TOKEN", ""))
    instantly_client = InstantlyClient(
        api_key=os.environ.get("INSTANTLY_API_KEY", ""),
    )

    llm = LLMClient(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    llm.set_agent("argus")
    if state_db_path.is_file():
        from devrel_swarm.project.cost_sink import make_sqlite_sink
        llm.set_cost_sink(make_sqlite_sink(state_db_path))

    return Argus(
        posthog_collector=PostHogCollector(posthog_client),
        github_collector=GitHubCollector(github_client),
        instantly_collector=InstantlyCollector(instantly_client),
        social_collector=SocialCollector(state_db_path),
        llm_client=llm,
        state_db_path=state_db_path,
    )


def _write_markdown_deliverable(
    report: PerformanceReport, deliverables_dir: Path,
) -> Path:
    deliverables_dir.mkdir(parents=True, exist_ok=True)
    out = deliverables_dir / f"analytics-{report.period_end.date().isoformat()}.md"
    out.write_text(report.to_markdown(), encoding="utf-8")
    return out


@analytics_app.command("history")
def history_command(
    content_id: str = typer.Argument(
        ..., help="Content ID to show history for (e.g., 'blog/cli-launch')."
    ),
    format_: str = typer.Option(
        "md", "--format", help="md or json."
    ),
) -> None:
    """Show the metric trajectory of one piece of content across reports."""
    paths = find_paths_or_exit(console)
    if format_ not in {"md", "json"}:
        raise typer.BadParameter("--format must be 'md' or 'json'")

    from devrel_swarm.project.state import open_db
    if not paths.state_db.is_file():
        console.print("[yellow]No state.db yet. Run 'devrel analytics report' first.[/yellow]")
        raise typer.Exit(code=1)

    with open_db(paths.state_db) as conn:
        rows = conn.execute(
            "SELECT period_end, primary_metric, metric_name, content_type "
            "FROM metric_history WHERE content_id = ? ORDER BY period_end ASC",
            (content_id,),
        ).fetchall()

    if not rows:
        console.print(f"[yellow]No history for content_id={content_id}[/yellow]")
        raise typer.Exit(code=1)

    if format_ == "json":
        sys.stdout.write(json.dumps(
            [
                {
                    "period_end": r["period_end"],
                    "primary_metric": r["primary_metric"],
                    "metric_name": r["metric_name"],
                    "content_type": r["content_type"],
                }
                for r in rows
            ],
            indent=2,
        ))
        sys.stdout.write("\n")
        return

    # Markdown
    lines = [f"# History for `{content_id}` ({rows[0]['content_type']})", ""]
    metric_name = rows[0]["metric_name"]
    lines.append(f"| period_end | {metric_name} | delta |")
    lines.append("|---|---|---|")
    prev = None
    for r in rows:
        v = r["primary_metric"]
        if prev is None:
            delta = "—"
        else:
            d = ((v - prev) / prev * 100) if prev else 0.0
            delta = f"{d:+.1f}%"
        lines.append(f"| {r['period_end'][:10]} | {v:g} | {delta} |")
        prev = v
    sys.stdout.write("\n".join(lines) + "\n")


@analytics_app.command("diff")
def diff_command(
    period_a: str = typer.Argument(..., help="Earlier period (YYYY-MM-DD or full ISO)."),
    period_b: str = typer.Argument(..., help="Later period (YYYY-MM-DD or full ISO)."),
    format_: str = typer.Option("md", "--format", help="md or json."),
    limit: int = typer.Option(20, "--limit", help="Top N changes by absolute delta."),
) -> None:
    """Compare two periods side-by-side. Shows the top movers (gainers + losers).

    Periods are matched against metric_history.period_end with a prefix
    match: '2026-04-25' matches any timestamp starting with that date.
    """
    paths = find_paths_or_exit(console)
    if format_ not in {"md", "json"}:
        raise typer.BadParameter("--format must be 'md' or 'json'")

    from devrel_swarm.project.state import open_db
    if not paths.state_db.is_file():
        console.print("[yellow]No state.db. Run 'devrel analytics report' first.[/yellow]")
        raise typer.Exit(code=1)

    with open_db(paths.state_db) as conn:
        a_rows = conn.execute(
            "SELECT content_id, primary_metric, metric_name, content_type "
            "FROM metric_history WHERE period_end LIKE ?",
            (f"{period_a}%",),
        ).fetchall()
        b_rows = conn.execute(
            "SELECT content_id, primary_metric, metric_name, content_type "
            "FROM metric_history WHERE period_end LIKE ?",
            (f"{period_b}%",),
        ).fetchall()

    if not a_rows or not b_rows:
        console.print(
            f"[yellow]No history for one or both periods (a={len(a_rows)}, b={len(b_rows)}).[/yellow]"
        )
        raise typer.Exit(code=1)

    a_by_id = {r["content_id"]: r for r in a_rows}
    b_by_id = {r["content_id"]: r for r in b_rows}

    rows: list[dict] = []
    for cid in set(a_by_id) | set(b_by_id):
        a_val = a_by_id.get(cid, {"primary_metric": None})["primary_metric"]
        b_val = b_by_id.get(cid, {"primary_metric": None})["primary_metric"]
        if a_val is None and b_val is not None:
            kind, delta_pct = "new", None
            sort_key = b_val
        elif a_val is not None and b_val is None:
            kind, delta_pct = "gone", None
            sort_key = a_val
        else:
            kind = "changed"
            delta_pct = (
                ((b_val - a_val) / a_val * 100.0) if a_val else 0.0
            )
            sort_key = abs(delta_pct)
        rows.append({
            "content_id": cid,
            "kind": kind,
            "a": a_val,
            "b": b_val,
            "delta_pct": delta_pct,
            "_sort": sort_key,
            "metric_name": (a_by_id.get(cid) or b_by_id[cid])["metric_name"],
        })

    rows.sort(key=lambda r: r["_sort"] or 0, reverse=True)
    rows = rows[:limit]

    if format_ == "json":
        payload = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        sys.stdout.write(json.dumps(payload, indent=2))
        sys.stdout.write("\n")
        return

    lines = [f"# Diff: {period_a} → {period_b}", ""]
    lines.append("| content_id | kind | a | b | delta |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        a_disp = f"{r['a']:g}" if r["a"] is not None else "—"
        b_disp = f"{r['b']:g}" if r["b"] is not None else "—"
        d_disp = f"{r['delta_pct']:+.1f}%" if r["delta_pct"] is not None else "—"
        lines.append(f"| {r['content_id']} | {r['kind']} | {a_disp} | {b_disp} | {d_disp} |")
    sys.stdout.write("\n".join(lines) + "\n")


@analytics_app.command("report")
def report_command(
    since: str = typer.Option(
        "7d", "--since", help="Lookback window (e.g., 7d, 30d, 12w)."
    ),
    format_: str = typer.Option(
        "md", "--format", help="stdout format: md or json."
    ),
    push: bool = typer.Option(
        False, "--push", help="Push the report to configured Slack/email."
    ),
) -> None:
    """Produce an Argus performance report for the last `--since` window."""
    paths = find_paths_or_exit(console)
    if format_ not in {"md", "json"}:
        raise typer.BadParameter("--format must be 'md' or 'json'")

    delta = _parse_since(since)
    end = datetime.now(timezone.utc)
    start = end - delta

    argus = _build_argus(paths.state_db)
    report = asyncio.run(argus.run(period_start=start, period_end=end))

    out_path = _write_markdown_deliverable(report, paths.deliverables_dir)
    err_console.print(f"[dim]Wrote deliverable: {out_path}[/dim]")

    if format_ == "json":
        sys.stdout.write(json.dumps(report.to_json(), indent=2, default=str))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(report.to_markdown())

    if push:
        try:
            asyncio.run(_push_report(report, end))
        except Exception as exc:  # noqa: BLE001
            err_console.print(f"[yellow]Push failed: {exc}[/yellow]")


async def _push_report(report: PerformanceReport, end: datetime) -> None:
    """Push the markdown report to Telegram + email if configured.

    Builds a fresh NotificationConfig from env vars; matches how
    devrel-swarm's other push paths construct the notification service.
    """
    import os

    from devrel_swarm.tools.notifications import (
        NotificationConfig,
        NotificationService,
    )

    config = NotificationConfig(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        email_sender=os.environ.get("EMAIL_SENDER", ""),
        email_password=os.environ.get("EMAIL_PASSWORD", ""),
        email_recipients=[
            r.strip()
            for r in os.environ.get("EMAIL_RECIPIENTS", "").split(",")
            if r.strip()
        ] or None,
    )
    svc = NotificationService(config)
    try:
        markdown = report.to_markdown()
        subject = f"Argus report — {end.date().isoformat()}"
        await svc.send_telegram(markdown[:4000])
        await svc.send_email(subject, f"<pre>{markdown}</pre>")
    finally:
        await svc.close()
