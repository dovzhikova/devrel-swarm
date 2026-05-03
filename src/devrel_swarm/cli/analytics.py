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
