"""Argus data collectors — one class per source.

Each collector exposes a single async method ``collect(period)`` returning
``list[PerformanceMetric]``. Collectors do not raise — failures are logged
and an empty list is returned, so Argus can mark the source unhealthy in
``PerformanceReport.sources_ok`` without aborting the whole report.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from devrel_swarm.core.argus import ContentType, PerformanceMetric

if TYPE_CHECKING:
    from devrel_swarm.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)

Period = tuple[datetime, datetime]

_LANDING_PATHS: frozenset[str] = frozenset(
    {"/", "/pricing", "/about", "/contact", "/features", "/docs"}
)


def _classify_url(url: str) -> ContentType:
    """Heuristic: /blog/* → blog; root + configured marketing paths → landing."""
    path = urlparse(url).path or "/"
    if path in _LANDING_PATHS:
        return "landing"
    if path.startswith("/blog/"):
        return "blog"
    return "landing"


def _content_id_from_url(url: str) -> str:
    """Stable id derived from URL path."""
    path = urlparse(url).path or "/"
    if path.startswith("/blog/"):
        slug = path[len("/blog/") :].rstrip("/")
        return f"blog/{slug}" if slug else "blog/index"
    return path


class PostHogCollector:
    """Pulls page-view + unique-visitor counts from PostHog grouped by URL."""

    def __init__(self, client: "PostHogClient"):
        self.client = client

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        _start, end = period
        try:
            rows = await self.client.fetch_events_by_url(start=_start, end=end)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostHogCollector failed: %s", exc)
            return []

        metrics: list[PerformanceMetric] = []
        for row in rows:
            url = row.get("url", "")
            if not url:
                continue
            metrics.append(
                PerformanceMetric(
                    content_id=_content_id_from_url(url),
                    content_type=_classify_url(url),
                    title=row.get("title") or url,
                    url=url,
                    published_at=end,
                    primary_metric=float(row.get("page_views", 0) or 0),
                    metric_name="page_views",
                    secondary_metrics={
                        "unique_visitors": float(row.get("unique_visitors", 0) or 0),
                    },
                )
            )
        return metrics


class GitHubCollector:
    """Emits one PerformanceMetric per repo with stars_delta as primary KPI.

    Wrapped client is expected to expose ``repo_full_name: str`` and
    ``async get_repo_stats() -> dict`` with at minimum
    ``stars, forks, open_issues, stars_delta_7d, issues_closed_7d``.
    """

    def __init__(self, client):
        self.client = client

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        _start, end = period
        try:
            stats = await self.client.get_repo_stats()
        except Exception as exc:  # noqa: BLE001
            logger.warning("GitHubCollector failed: %s", exc)
            return []

        repo = getattr(self.client, "repo_full_name", "unknown/unknown")
        return [
            PerformanceMetric(
                content_id=f"repo/{repo}",
                content_type="repo",
                title=repo,
                url=f"https://github.com/{repo}",
                published_at=end,
                primary_metric=float(stats.get("stars_delta_7d", 0) or 0),
                metric_name="stars_delta",
                secondary_metrics={
                    "stars_total": float(stats.get("stars", 0) or 0),
                    "forks": float(stats.get("forks", 0) or 0),
                    "open_issues": float(stats.get("open_issues", 0) or 0),
                    "issues_closed": float(stats.get("issues_closed_7d", 0) or 0),
                },
            )
        ]


def _row_in_period(row: dict, start: datetime, end: datetime) -> bool:
    """True if row's created_at/updated_at falls in [start, end].

    Falls back to True (include the row) when neither timestamp is present —
    older Instantly campaigns may not include either field. Without this
    filter, --since 7d and --since 90d return identical metrics.
    """
    raw = row.get("created_at") or row.get("updated_at")
    if not raw:
        return True
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    return start <= when <= end


class InstantlyCollector:
    """One PerformanceMetric per email campaign; reply_rate is primary KPI.

    Wrapped client is expected to expose
    ``async list_campaigns_with_analytics() -> list[dict]`` with at minimum
    ``id, name, sent, opens, clicks, replies, open_rate, reply_rate``,
    and optionally ``created_at`` / ``updated_at`` for period filtering.
    """

    def __init__(self, client):
        self.client = client

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        start, end = period
        try:
            rows = await self.client.list_campaigns_with_analytics()
        except Exception as exc:  # noqa: BLE001
            logger.warning("InstantlyCollector failed: %s", exc)
            return []

        metrics: list[PerformanceMetric] = []
        for row in rows:
            cid = row.get("id") or ""
            if not cid:
                continue
            if not _row_in_period(row, start, end):
                continue
            metrics.append(
                PerformanceMetric(
                    content_id=f"email/{cid}",
                    content_type="email",
                    title=row.get("name", cid),
                    url=None,
                    published_at=end,
                    primary_metric=float(row.get("reply_rate", 0.0) or 0.0),
                    metric_name="reply_rate",
                    secondary_metrics={
                        "sent": float(row.get("sent", 0) or 0),
                        "opens": float(row.get("opens", 0) or 0),
                        "clicks": float(row.get("clicks", 0) or 0),
                        "replies": float(row.get("replies", 0) or 0),
                        "open_rate": float(row.get("open_rate", 0.0) or 0.0),
                    },
                )
            )
        return metrics


class SocialCollector:
    """Reads Echo's ``social_mentions`` table, filters to ``is_own_post=1``,
    emits one metric per post with engagement_score as the primary KPI.

    Returns an empty list (and logs) if the table is missing or the period
    yields no rows. Does not raise.
    """

    # Pinned schema contract: these columns MUST exist on Echo's
    # social_mentions table. If a future Echo migration renames any of
    # these, _verify_schema logs a clear warning and the collector returns
    # [] rather than silently producing partial data.
    _REQUIRED_COLUMNS: frozenset[str] = frozenset(
        {
            "platform",
            "post_id",
            "title",
            "url",
            "posted_at",
            "upvotes",
            "comments",
            "engagement_score",
            "is_own_post",
        }
    )

    def __init__(self, state_db_path: Path):
        self.state_db_path = state_db_path
        self._schema_verified = False

    def _verify_schema(self, conn: sqlite3.Connection) -> bool:
        """Confirm social_mentions has all required columns.

        Cached per instance via ``self._schema_verified`` so we only PRAGMA
        once. Logs a single clear warning if columns are missing/renamed.
        """
        if self._schema_verified:
            return True
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(social_mentions)")}
        except sqlite3.OperationalError:
            return False  # table doesn't exist yet
        missing = self._REQUIRED_COLUMNS - cols
        if missing:
            logger.warning(
                "SocialCollector: Echo's social_mentions table is missing "
                "required columns: %s. Argus will return no social metrics "
                "until the schema is updated.",
                sorted(missing),
            )
            return False
        self._schema_verified = True
        return True

    def _read_rows(self, start_iso: str, end_iso: str):
        """Synchronous SQLite read; called via asyncio.to_thread to avoid
        stalling the event loop. Returns None on any failure (the async
        wrapper translates that to an empty result + logs)."""
        try:
            with sqlite3.connect(self.state_db_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._verify_schema(conn):
                    return None
                return conn.execute(
                    "SELECT platform, post_id, title, url, posted_at, "
                    "upvotes, comments, engagement_score "
                    "FROM social_mentions "
                    "WHERE is_own_post = 1 AND posted_at >= ? AND posted_at <= ?",
                    (start_iso, end_iso),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.info("SocialCollector: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("SocialCollector failed: %s", exc)
            return None

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        start, end = period
        if not self.state_db_path.is_file():
            logger.info("SocialCollector: state.db not present, skipping")
            return []

        rows = await asyncio.to_thread(
            self._read_rows,
            start.isoformat(),
            end.isoformat(),
        )
        if rows is None:
            return []

        metrics: list[PerformanceMetric] = []
        for row in rows:
            try:
                posted_at = datetime.fromisoformat(row["posted_at"].replace("Z", "+00:00"))
            except ValueError:
                posted_at = end
            metrics.append(
                PerformanceMetric(
                    content_id=f"social/{row['platform']}/{row['post_id']}",
                    content_type="social",
                    title=row["title"] or row["post_id"],
                    url=row["url"],
                    published_at=posted_at,
                    primary_metric=float(row["engagement_score"] or 0.0),
                    metric_name="engagement_score",
                    secondary_metrics={
                        "upvotes": float(row["upvotes"] or 0),
                        "comments": float(row["comments"] or 0),
                    },
                )
            )
        return metrics
