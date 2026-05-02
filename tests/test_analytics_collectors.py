"""Tests for Argus's per-source data collectors."""

from __future__ import annotations

import sqlite3 as _sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.argus import PerformanceMetric
from devrel_swarm.tools.analytics import (
    GitHubCollector,
    InstantlyCollector,
    PostHogCollector,
    SocialCollector,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ───────────────────────── PostHogCollector ─────────────────────────


@pytest.mark.asyncio
async def test_posthog_collector_returns_per_url_pageviews():
    fake_client = MagicMock()
    fake_client.fetch_events_by_url = AsyncMock(
        return_value=[
            {
                "url": "https://example.com/blog/cli-launch",
                "title": "CLI launch",
                "page_views": 5400,
                "unique_visitors": 3200,
            },
            {
                "url": "https://example.com/blog/python-testing",
                "title": "Python testing",
                "page_views": 1200,
                "unique_visitors": 800,
            },
        ]
    )

    collector = PostHogCollector(fake_client)
    end = _utc_now()
    start = end - timedelta(days=7)
    metrics = await collector.collect((start, end))

    assert len(metrics) == 2
    assert all(isinstance(m, PerformanceMetric) for m in metrics)
    by_id = {m.content_id: m for m in metrics}

    cli = by_id["blog/cli-launch"]
    assert cli.content_type == "blog"
    assert cli.metric_name == "page_views"
    assert cli.primary_metric == 5400.0
    assert cli.secondary_metrics["unique_visitors"] == 3200.0
    assert cli.url == "https://example.com/blog/cli-launch"


@pytest.mark.asyncio
async def test_posthog_collector_classifies_landing_vs_blog():
    fake_client = MagicMock()
    fake_client.fetch_events_by_url = AsyncMock(
        return_value=[
            {"url": "https://example.com/", "title": "Home", "page_views": 999, "unique_visitors": 500},
            {"url": "https://example.com/pricing", "title": "Pricing", "page_views": 444, "unique_visitors": 200},
            {"url": "https://example.com/blog/x", "title": "X", "page_views": 100, "unique_visitors": 50},
        ]
    )
    collector = PostHogCollector(fake_client)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    types = {m.content_id: m.content_type for m in metrics}
    assert types["/"] == "landing"
    assert types["/pricing"] == "landing"
    assert types["blog/x"] == "blog"


@pytest.mark.asyncio
async def test_posthog_collector_handles_empty():
    fake_client = MagicMock()
    fake_client.fetch_events_by_url = AsyncMock(return_value=[])
    collector = PostHogCollector(fake_client)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    assert metrics == []


@pytest.mark.asyncio
async def test_posthog_collector_returns_empty_on_error():
    fake_client = MagicMock()
    fake_client.fetch_events_by_url = AsyncMock(side_effect=RuntimeError("api down"))
    collector = PostHogCollector(fake_client)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    assert metrics == []


# ───────────────────────── GitHubCollector ─────────────────────────


@pytest.mark.asyncio
async def test_github_collector_emits_repo_metric():
    fake = MagicMock()
    fake.get_repo_stats = AsyncMock(
        return_value={
            "stars": 1234,
            "forks": 56,
            "open_issues": 12,
            "stars_delta_7d": 45,
            "issues_closed_7d": 8,
        }
    )
    fake.repo_full_name = "openclaw/openclaw"
    collector = GitHubCollector(fake)

    end = _utc_now()
    metrics = await collector.collect((end - timedelta(days=7), end))
    assert len(metrics) == 1
    m = metrics[0]
    assert m.content_id == "repo/openclaw/openclaw"
    assert m.content_type == "repo"
    assert m.metric_name == "stars_delta"
    assert m.primary_metric == 45.0
    assert m.secondary_metrics["forks"] == 56.0
    assert m.secondary_metrics["issues_closed"] == 8.0


@pytest.mark.asyncio
async def test_github_collector_returns_empty_on_error():
    fake = MagicMock()
    fake.get_repo_stats = AsyncMock(side_effect=RuntimeError("api down"))
    fake.repo_full_name = "openclaw/openclaw"
    collector = GitHubCollector(fake)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    assert metrics == []


# ───────────────────────── InstantlyCollector ─────────────────────────


@pytest.mark.asyncio
async def test_instantly_collector_emits_per_campaign_metrics():
    fake = MagicMock()
    fake.list_campaigns_with_analytics = AsyncMock(
        return_value=[
            {
                "id": "camp-1", "name": "Q2 outbound", "sent": 1000,
                "opens": 350, "clicks": 80, "replies": 25,
                "open_rate": 0.35, "reply_rate": 0.025,
            },
            {
                "id": "camp-2", "name": "Founder series", "sent": 500,
                "opens": 100, "clicks": 30, "replies": 50,
                "open_rate": 0.20, "reply_rate": 0.10,
            },
        ]
    )
    collector = InstantlyCollector(fake)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))

    by_id = {m.content_id: m for m in metrics}
    founder = by_id["email/camp-2"]
    assert founder.content_type == "email"
    assert founder.metric_name == "reply_rate"
    assert founder.primary_metric == pytest.approx(0.10)
    assert founder.secondary_metrics["sent"] == 500.0
    assert founder.secondary_metrics["open_rate"] == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_instantly_collector_returns_empty_on_error():
    fake = MagicMock()
    fake.list_campaigns_with_analytics = AsyncMock(side_effect=RuntimeError("rate limited"))
    collector = InstantlyCollector(fake)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    assert metrics == []


# ───────────────────────── SocialCollector ─────────────────────────


def _seed_social_mentions_db(db_path):
    """Build a minimal social_mentions table the way Echo writes it.

    Schema mirrored here so Argus's read contract is independent of
    Echo's migration timing.
    """
    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_mentions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                post_id TEXT NOT NULL,
                title TEXT,
                url TEXT,
                posted_at TEXT NOT NULL,
                upvotes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                engagement_score REAL DEFAULT 0,
                is_own_post INTEGER DEFAULT 0
            )
            """
        )
        conn.executemany(
            "INSERT INTO social_mentions "
            "(platform, post_id, title, url, posted_at, upvotes, comments, "
            "engagement_score, is_own_post) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("reddit", "abc1", "Why CLI tools win",
                 "https://reddit.com/r/programming/abc1",
                 "2026-04-30T10:00:00+00:00", 240, 35, 87.5, 1),
                ("hackernews", "hn-9", "Show HN: devrel-swarm",
                 "https://news.ycombinator.com/item?id=9",
                 "2026-04-29T14:00:00+00:00", 150, 42, 76.0, 1),
                ("reddit", "noise-1", "Random unrelated post",
                 "https://reddit.com/r/x/noise-1",
                 "2026-04-28T08:00:00+00:00", 5, 1, 6.0, 0),
            ],
        )
        conn.commit()


@pytest.mark.asyncio
async def test_social_collector_reads_only_own_posts(tmp_path):
    db = tmp_path / "state.db"
    _seed_social_mentions_db(db)
    collector = SocialCollector(db)
    metrics = await collector.collect(
        (
            datetime(2026, 4, 25, tzinfo=timezone.utc),
            datetime(2026, 5, 2, tzinfo=timezone.utc),
        )
    )
    ids = {m.content_id for m in metrics}
    assert ids == {"social/reddit/abc1", "social/hackernews/hn-9"}
    by_id = {m.content_id: m for m in metrics}
    reddit = by_id["social/reddit/abc1"]
    assert reddit.content_type == "social"
    assert reddit.metric_name == "engagement_score"
    assert reddit.primary_metric == pytest.approx(87.5)
    assert reddit.secondary_metrics["upvotes"] == 240.0


@pytest.mark.asyncio
async def test_social_collector_returns_empty_when_table_missing(tmp_path):
    db = tmp_path / "state.db"
    db.touch()
    collector = SocialCollector(db)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    assert metrics == []
