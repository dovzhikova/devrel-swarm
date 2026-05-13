"""`devrel argus report`: Argus performance report.

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

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.core.argus import (
    Argus,
    PerformanceReport,
    compute_calibration,
    write_recommendation_briefs,
)

console = Console()
err_console = Console(stderr=True)

argus_app = typer.Typer(
    name="argus",
    help="Content performance analysis (Argus).",
    no_args_is_help=True,
)


_SINCE_RE = re.compile(r"^(\d+)([dwmy])$")


def _parse_since(since: str) -> timedelta:
    """Accept '7d' / '30d' / '12w' / '3m' / '1y'."""
    m = _SINCE_RE.match(since.strip())
    if not m:
        raise typer.BadParameter(f"--since must look like '7d', '30d', '12w': got {since!r}")
    n, unit = int(m.group(1)), m.group(2)
    days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    return timedelta(days=n * days)


def _build_argus(state_db_path: Path) -> Argus:
    """Construct Argus with real collectors. Patched in unit tests."""
    import os

    from devrel_origin.core.llm import LLMClient
    from devrel_origin.tools.analytics import (
        GitHubCollector,
        InstantlyCollector,
        PostHogCollector,
        SocialCollector,
    )
    from devrel_origin.tools.api_client import PostHogClient
    from devrel_origin.tools.github_tools import GitHubTools
    from devrel_origin.tools.instantly_client import InstantlyClient

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
        from devrel_origin.project.cost_sink import make_sqlite_sink

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
    report: PerformanceReport,
    deliverables_dir: Path,
) -> Path:
    deliverables_dir.mkdir(parents=True, exist_ok=True)
    out = deliverables_dir / f"analytics-{report.period_end.date().isoformat()}.md"
    out.write_text(report.to_markdown(), encoding="utf-8")
    return out


@argus_app.command("summary")
def summary_command(
    root: str = typer.Option(
        "~", "--root", help="Root to scan for .devrel/ directories. Default: $HOME."
    ),
    format_: str = typer.Option("md", "--format", help="md or json."),
    max_depth: int = typer.Option(
        4, "--max-depth", help="Max directory depth to descend (avoids slow $HOME walks)."
    ),
) -> None:
    """Aggregate Argus reports across every .devrel/ project under a root.

    Walks the filesystem looking for ``.devrel/state.db`` files (capped at
    --max-depth) and reports total spend, total recommendations, and the
    most recent report per project.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        console.print(f"[red]{root_path} is not a directory.[/red]")
        raise typer.Exit(code=1)

    projects: list[dict] = []
    for state_db in _walk_for_state_dbs(root_path, max_depth):
        info = _summarize_project_db(state_db)
        if info:
            projects.append(info)

    if format_ == "json":
        sys.stdout.write(json.dumps(projects, indent=2, default=str))
        sys.stdout.write("\n")
        return

    if format_ != "md":
        raise typer.BadParameter("--format must be 'md' or 'json'")

    lines = [f"# Argus cross-project summary: {len(projects)} projects under {root_path}", ""]
    if not projects:
        lines.append("_No .devrel/state.db files found._")
        sys.stdout.write("\n".join(lines) + "\n")
        return
    lines.append("| project | last_report | total_recs | total_metrics | spend_usd |")
    lines.append("|---|---|---|---|---|")
    for p in sorted(projects, key=lambda x: x["last_report"] or "", reverse=True):
        lines.append(
            f"| {p['project']} | {(p['last_report'] or '-')[:10]} | "
            f"{p['total_recs']} | {p['total_metrics']} | ${p['spend_usd']:.2f} |"
        )
    sys.stdout.write("\n".join(lines) + "\n")


def _walk_for_state_dbs(root: Path, max_depth: int):
    """Yield every state.db at root/**/.devrel/state.db up to max_depth.

    Skips dot-directories other than .devrel (so ~/.cache, ~/.config etc
    don't slow the walk to a crawl)."""

    def _walk(dir_: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = list(dir_.iterdir())
        except PermissionError:
            return
        for child in entries:
            if not child.is_dir():
                continue
            if child.name == ".devrel":
                state_db = child / "state.db"
                if state_db.is_file():
                    yield state_db
                continue
            if child.name.startswith("."):
                continue
            yield from _walk(child, depth + 1)

    yield from _walk(root, 0)


def _summarize_project_db(state_db: Path) -> dict | None:
    """Return per-project rollup or None if the DB has no Argus tables."""
    try:
        from devrel_origin.project.state import open_db

        with open_db(state_db) as conn:
            try:
                last_row = conn.execute(
                    "SELECT MAX(period_end) AS p FROM analytics_reports"
                ).fetchone()
                rec_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM analytics_recommendations"
                ).fetchone()
                hist_row = conn.execute("SELECT COUNT(*) AS c FROM metric_history").fetchone()
            except Exception:  # noqa: BLE001: table missing means not an Argus project
                return None
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM costs WHERE agent = 'argus'"
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None

    return {
        "project": str(state_db.parent.parent),
        "last_report": last_row["p"] if last_row else None,
        "total_recs": int(rec_row["c"]) if rec_row else 0,
        "total_metrics": int(hist_row["c"]) if hist_row else 0,
        "spend_usd": float(cost_row["total"]) if cost_row else 0.0,
    }


@argus_app.command("history")
def history_command(
    content_id: str = typer.Argument(
        ..., help="Content ID to show history for (e.g., 'blog/cli-launch')."
    ),
    format_: str = typer.Option("md", "--format", help="md or json."),
) -> None:
    """Show the metric trajectory of one piece of content across reports."""
    paths = find_paths_or_exit(console)
    if format_ not in {"md", "json"}:
        raise typer.BadParameter("--format must be 'md' or 'json'")

    from devrel_origin.project.state import open_db

    if not paths.state_db.is_file():
        console.print("[yellow]No state.db yet. Run 'devrel argus report' first.[/yellow]")
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
        sys.stdout.write(
            json.dumps(
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
            )
        )
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
            delta = "-"
        else:
            d = ((v - prev) / prev * 100) if prev else 0.0
            delta = f"{d:+.1f}%"
        lines.append(f"| {r['period_end'][:10]} | {v:g} | {delta} |")
        prev = v
    sys.stdout.write("\n".join(lines) + "\n")


@argus_app.command("diff")
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

    from devrel_origin.project.state import open_db

    if not paths.state_db.is_file():
        console.print("[yellow]No state.db. Run 'devrel argus report' first.[/yellow]")
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
            delta_pct = ((b_val - a_val) / a_val * 100.0) if a_val else 0.0
            sort_key = abs(delta_pct)
        rows.append(
            {
                "content_id": cid,
                "kind": kind,
                "a": a_val,
                "b": b_val,
                "delta_pct": delta_pct,
                "_sort": sort_key,
                "metric_name": (a_by_id.get(cid) or b_by_id[cid])["metric_name"],
            }
        )

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
        a_disp = f"{r['a']:g}" if r["a"] is not None else "-"
        b_disp = f"{r['b']:g}" if r["b"] is not None else "-"
        d_disp = f"{r['delta_pct']:+.1f}%" if r["delta_pct"] is not None else "-"
        lines.append(f"| {r['content_id']} | {r['kind']} | {a_disp} | {b_disp} | {d_disp} |")
    sys.stdout.write("\n".join(lines) + "\n")


@argus_app.command("calibration")
def calibration_command(
    format_: str = typer.Option("md", "--format", help="md or json."),
) -> None:
    """Show how well past Argus recommendations have actually panned out.

    Scores each historical double_down/retire recommendation against the
    metric_history observations recorded after first_seen_period. Other
    actions are counted as 'unscored' (no clean post-hoc test).
    """
    paths = find_paths_or_exit(console)
    if format_ not in {"md", "json"}:
        raise typer.BadParameter("--format must be 'md' or 'json'")

    if not paths.state_db.is_file():
        console.print("[yellow]No state.db. Run 'devrel argus report' first.[/yellow]")
        raise typer.Exit(code=1)

    cal = compute_calibration(paths.state_db)
    if format_ == "json":
        sys.stdout.write(json.dumps(cal, indent=2))
        sys.stdout.write("\n")
        return

    lines = ["# Argus calibration", ""]
    lines.append(f"- scored recommendations: **{cal['scored_recs']}**")
    lines.append(
        f"- unscored (insufficient post-period data or non-scoreable action): {cal['unscored_recs']}"
    )
    if cal.get("high_conf_rate") is not None:
        lines.append(f"- high-confidence (≥0.8) hit rate: {cal['high_conf_rate']:.0%}")
    if cal.get("low_conf_rate") is not None:
        lines.append(f"- low-confidence (<0.5) hit rate: {cal['low_conf_rate']:.0%}")
    lines.append("")
    if not cal["by_action"]:
        lines.append(
            "_No scored recommendations yet. Calibration needs at least one rec with metric_history rows after its first_seen_period._"
        )
    else:
        lines.append("| action | n | panned_out | rate | avg_conf | lift vs coin-flip |")
        lines.append("|---|---|---|---|---|---|")
        for action, stats in sorted(cal["by_action"].items()):
            lines.append(
                f"| {action} | {stats['n']} | {stats['panned_out']} | "
                f"{stats['rate']:.0%} | {stats['avg_confidence']:.2f} | "
                f"{stats['calibrated_lift']:+.2f} |"
            )
    sys.stdout.write("\n".join(lines) + "\n")


@argus_app.command("report")
def report_command(
    since: str = typer.Option("7d", "--since", help="Lookback window (e.g., 7d, 30d, 12w)."),
    format_: str = typer.Option("md", "--format", help="stdout format: md or json."),
    push: bool = typer.Option(False, "--push", help="Push the report to configured Slack/email."),
    push_on_partial: bool = typer.Option(
        False,
        "--push-on-partial",
        help="Override the all-green push gate. Push even if some sources failed.",
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

    brief_paths = write_recommendation_briefs(report, paths.deliverables_dir)
    if brief_paths:
        err_console.print(
            f"[dim]Wrote {len(brief_paths)} content brief(s) for actionable recs[/dim]"
        )

    if format_ == "json":
        sys.stdout.write(json.dumps(report.to_json(), indent=2, default=str))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(report.to_markdown())

    if push:
        failed_sources = [k for k, v in report.sources_ok.items() if not v]
        if failed_sources and not push_on_partial:
            err_console.print(
                f"[yellow]Skipping push: data is partial (failed sources: "
                f"{', '.join(failed_sources)}). Pass --push-on-partial to override.[/yellow]"
            )
        else:
            try:
                asyncio.run(_push_report(report, end))
            except Exception as exc:  # noqa: BLE001
                err_console.print(f"[yellow]Push failed: {exc}[/yellow]")


async def _push_report(report: PerformanceReport, end: datetime) -> None:
    """Push the markdown report to Telegram + email if configured.

    Builds a fresh NotificationConfig from env vars; matches how
    devrel-origin's other push paths construct the notification service.
    """
    import os

    from devrel_origin.tools.notifications import (
        NotificationConfig,
        NotificationService,
    )

    config = NotificationConfig(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        email_sender=os.environ.get("EMAIL_SENDER", ""),
        email_password=os.environ.get("EMAIL_PASSWORD", ""),
        email_recipients=[
            r.strip() for r in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if r.strip()
        ]
        or None,
    )
    svc = NotificationService(config)
    try:
        markdown = report.to_markdown()
        subject = f"Argus report: {end.date().isoformat()}"
        await svc.send_telegram(markdown[:4000])
        await svc.send_email(subject, f"<pre>{markdown}</pre>")
    finally:
        await svc.close()
