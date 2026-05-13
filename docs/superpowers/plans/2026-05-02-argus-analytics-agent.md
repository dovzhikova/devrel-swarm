# Argus Analytics Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 13th agent — Argus — a post-publish content performance analyst that pulls from PostHog/GitHub/Instantly/Echo's social_mentions, scores deterministically, and emits structured optimization recommendations via a single Sonnet call.

**Architecture:** New `argus.py` agent module that orchestrates 4 new collectors in `tools/analytics.py`. Pure-Python `_score_metrics` does the math; one cached-system-prompt LLM call generates `Recommendation` dataclasses. New SQLite table `analytics_reports` stores reports for historical baselines. New `analytics report` CLI verb (subgroup `analytics`). Optional Atlas stage between Sentinel and OKR compilation, gated by `[orchestration].analytics_in_run`.

**Tech Stack:** Python 3.12 async, dataclasses, httpx via existing API clients, SQLite via existing `state.py` helpers, Typer CLI, pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-05-02-argus-analytics-agent-design.md`

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/devrel_origin/core/argus.py` | Create | Agent class, dataclass schemas, `_score_metrics`, `_generate_recommendations`, `to_markdown` / `to_json` |
| `src/devrel_origin/tools/analytics.py` | Create | Four collector classes (PostHog, GitHub, Instantly, Social) |
| `src/devrel_origin/project/state.py` | Modify | Bump `SCHEMA_VERSION` to 2, add `analytics_reports` table |
| `src/devrel_origin/cli/analytics.py` | Create | `analytics report` Typer subgroup |
| `src/devrel_origin/cli/__init__.py` | Modify | Register `analytics_app` |
| `src/devrel_origin/core/atlas.py` | Modify | Optional Argus call after Sentinel stage, gated by config |
| `src/devrel_origin/core/agent_config.py` | Modify | Add `analytics_in_run: bool = True` to `AgentConfig.orchestration` (or equivalent) |
| `src/devrel_origin/core/__init__.py` | Modify | Export `Argus` |
| `tests/test_argus.py` | Create | Unit tests for scorer, dataclass round-trip, LLM-mocked integration |
| `tests/test_analytics_collectors.py` | Create | Per-collector tests with respx + sqlite mocks |
| `tests/cli/test_analytics_command.py` | Create | CLI verb test |
| `tests/test_atlas.py` | Modify | Add Atlas stage-integration test |
| `tests/project/test_state.py` (or equivalent) | Modify | Migration test for v1 → v2 |

---

## Task 1: Schemas — define dataclasses in `argus.py`

**Files:**
- Create: `src/devrel_origin/core/argus.py`
- Test: `tests/test_argus.py`

- [ ] **Step 1: Write the failing test for schema construction + serialization**

```python
# tests/test_argus.py
"""Unit tests for Argus content performance analyst agent."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from devrel_origin.core.argus import (
    PerformanceMetric,
    PerformanceReport,
    Recommendation,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_performance_metric_defaults():
    m = PerformanceMetric(
        content_id="blog/2026-04-29-cli-launch",
        content_type="blog",
        title="CLI launch",
        url="https://example.com/blog/cli-launch",
        published_at=_utc(2026, 4, 29),
        primary_metric=1234.0,
        metric_name="page_views",
    )
    assert m.secondary_metrics == {}
    assert m.percentile is None
    assert m.wow_delta is None
    assert m.anomaly_flag is False


def test_recommendation_required_fields():
    r = Recommendation(
        action="double_down",
        target="theme:python-testing",
        target_type="theme",
        rationale="Python testing posts have 3x corpus baseline page views.",
        evidence=["blog/python-testing-1: 5400 views (p95)", "blog/python-testing-2: 4800 views (p92)"],
        confidence=0.85,
    )
    d = asdict(r)
    assert d["action"] == "double_down"
    assert d["confidence"] == 0.85


def test_performance_report_round_trip():
    metric = PerformanceMetric(
        content_id="blog/x",
        content_type="blog",
        title="X",
        url=None,
        published_at=_utc(2026, 4, 1),
        primary_metric=100.0,
        metric_name="page_views",
    )
    rec = Recommendation(
        action="retire",
        target="blog/x",
        target_type="content",
        rationale="Bottom decile for 4 weeks.",
        evidence=["blog/x: 100 views (p5)"],
        confidence=0.7,
    )
    report = PerformanceReport(
        period_start=_utc(2026, 4, 25),
        period_end=_utc(2026, 5, 2),
        top_performers=[],
        bottom_performers=[metric],
        trend_signals=["Python topic +30% WoW"],
        recommendations=[rec],
        sources_ok={"posthog": True, "github": True, "instantly": False, "social": True},
    )
    assert report.insufficient_data is False
    assert report.llm_error is None
    assert report.sources_ok["instantly"] is False
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
cd /Users/macmini/devrel-origin && pytest tests/test_argus.py -v
```

Expected: `ImportError` / `ModuleNotFoundError: No module named 'devrel_origin.core.argus'`.

- [ ] **Step 3: Implement the schemas**

```python
# src/devrel_origin/core/argus.py
"""
Argus — Content Performance Analyst Agent.

Pulls post-publish performance data from PostHog, GitHub, Instantly, and
Echo's social_mentions table; ranks content deterministically; and emits
structured optimization recommendations via a single Sonnet call.

Sits beside Watchdog (infra) and Sentinel (pre-publish) as the
post-publish watcher in the 13-agent pantheon.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

logger = logging.getLogger(__name__)

ContentType = Literal["blog", "landing", "social", "email", "repo", "video"]
RecAction = Literal[
    "double_down", "retire", "rewrite", "retest", "amplify", "investigate",
]
TargetType = Literal["content", "theme", "channel"]


@dataclass
class PerformanceMetric:
    """Single content piece's performance snapshot for one period."""

    content_id: str
    content_type: ContentType
    title: str
    url: str | None
    published_at: datetime
    primary_metric: float
    metric_name: str
    secondary_metrics: dict[str, float] = field(default_factory=dict)
    percentile: float | None = None
    wow_delta: float | None = None
    anomaly_flag: bool = False


@dataclass
class Recommendation:
    """One optimization recommendation tied to one target."""

    action: RecAction
    target: str
    target_type: TargetType
    rationale: str
    evidence: list[str]
    confidence: float


@dataclass
class PerformanceReport:
    """Full Argus run output. Serialized to .devrel/state.db and to markdown."""

    period_start: datetime
    period_end: datetime
    top_performers: list[PerformanceMetric]
    bottom_performers: list[PerformanceMetric]
    trend_signals: list[str]
    recommendations: list[Recommendation]
    sources_ok: dict[str, bool]
    insufficient_data: bool = False
    llm_error: str | None = None
```

- [ ] **Step 4: Run the test and verify it passes**

```bash
pytest tests/test_argus.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/argus.py tests/test_argus.py
git commit -m "feat(argus): schemas — PerformanceMetric, Recommendation, PerformanceReport"
```

---

## Task 2: Deterministic scorer (`_score_metrics`)

**Files:**
- Modify: `src/devrel_origin/core/argus.py`
- Test: `tests/test_argus.py`

- [ ] **Step 1: Write failing tests for scoring**

Append to `tests/test_argus.py`:

```python
from devrel_origin.core.argus import _score_metrics


def _metric(content_id: str, content_type: str, value: float) -> PerformanceMetric:
    return PerformanceMetric(
        content_id=content_id,
        content_type=content_type,  # type: ignore[arg-type]
        title=content_id,
        url=None,
        published_at=_utc(2026, 4, 1),
        primary_metric=value,
        metric_name="page_views",
    )


def test_scorer_assigns_percentile_within_content_type():
    metrics = [
        _metric("blog/a", "blog", 10.0),
        _metric("blog/b", "blog", 50.0),
        _metric("blog/c", "blog", 90.0),
    ]
    scored = _score_metrics(metrics, baseline_by_type={})
    by_id = {m.content_id: m for m in scored}
    # Highest gets ~100, lowest gets ~0; middle near 50
    assert by_id["blog/c"].percentile == pytest.approx(100.0, abs=1.0)
    assert by_id["blog/a"].percentile == pytest.approx(0.0, abs=1.0)
    assert 30.0 < by_id["blog/b"].percentile < 70.0


def test_scorer_keeps_content_types_independent():
    metrics = [
        _metric("blog/a", "blog", 100.0),
        _metric("email/x", "email", 5.0),
    ]
    scored = _score_metrics(metrics, baseline_by_type={})
    by_id = {m.content_id: m for m in scored}
    # Both are the only entry in their type → both rank at 100th percentile
    assert by_id["blog/a"].percentile == pytest.approx(100.0, abs=1.0)
    assert by_id["email/x"].percentile == pytest.approx(100.0, abs=1.0)


def test_scorer_flags_anomaly_when_zscore_high():
    metrics = [_metric(f"blog/{i}", "blog", 10.0) for i in range(10)]
    metrics.append(_metric("blog/spike", "blog", 1000.0))  # extreme outlier
    scored = _score_metrics(metrics, baseline_by_type={})
    spike = next(m for m in scored if m.content_id == "blog/spike")
    assert spike.anomaly_flag is True


def test_scorer_computes_wow_delta_against_baseline():
    metrics = [_metric("blog/a", "blog", 200.0)]
    # baseline_by_type maps content_id -> prior period primary_metric
    scored = _score_metrics(
        metrics,
        baseline_by_type={"blog/a": 100.0},
    )
    a = scored[0]
    assert a.wow_delta == pytest.approx(100.0)  # +100% WoW
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_argus.py -v -k scorer
```

Expected: 4 FAIL with `ImportError: cannot import name '_score_metrics'`.

- [ ] **Step 3: Implement the scorer**

Append to `src/devrel_origin/core/argus.py`:

```python
import statistics


_ANOMALY_Z_THRESHOLD = 2.5


def _score_metrics(
    metrics: list[PerformanceMetric],
    *,
    baseline_by_type: dict[str, float],
) -> list[PerformanceMetric]:
    """Annotate each metric with percentile (vs same content_type peers in
    this batch), wow_delta (vs ``baseline_by_type[content_id]`` if present),
    and anomaly_flag (|z-score| > _ANOMALY_Z_THRESHOLD).

    Pure function — input metrics are not mutated; new instances are returned.
    """
    by_type: dict[str, list[PerformanceMetric]] = {}
    for m in metrics:
        by_type.setdefault(m.content_type, []).append(m)

    out: list[PerformanceMetric] = []
    for ctype, group in by_type.items():
        values = [m.primary_metric for m in group]
        n = len(values)
        mean = statistics.fmean(values) if values else 0.0
        stdev = statistics.pstdev(values) if n > 1 else 0.0

        for m in group:
            # Percentile rank: fraction of peers with strictly-lower value.
            # Single-item groups → 100.0 (only one peer, that's itself).
            if n <= 1:
                pct = 100.0
            else:
                lower = sum(1 for v in values if v < m.primary_metric)
                pct = (lower / (n - 1)) * 100.0

            # WoW delta: % change vs baseline (None if no baseline)
            baseline = baseline_by_type.get(m.content_id)
            if baseline is None or baseline == 0:
                wow = None
            else:
                wow = ((m.primary_metric - baseline) / baseline) * 100.0

            # Anomaly: z-score against group mean/stdev
            anomaly = False
            if stdev > 0:
                z = (m.primary_metric - mean) / stdev
                anomaly = abs(z) > _ANOMALY_Z_THRESHOLD

            out.append(
                PerformanceMetric(
                    content_id=m.content_id,
                    content_type=m.content_type,
                    title=m.title,
                    url=m.url,
                    published_at=m.published_at,
                    primary_metric=m.primary_metric,
                    metric_name=m.metric_name,
                    secondary_metrics=dict(m.secondary_metrics),
                    percentile=round(pct, 2),
                    wow_delta=round(wow, 2) if wow is not None else None,
                    anomaly_flag=anomaly,
                )
            )
    return out
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_argus.py -v
```

Expected: 7 PASSED (3 from Task 1 + 4 from Task 2).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/argus.py tests/test_argus.py
git commit -m "feat(argus): deterministic scorer (percentile, wow_delta, anomaly_flag)"
```

---

## Task 3: SQLite migration — `analytics_reports` table

**Files:**
- Modify: `src/devrel_origin/project/state.py:1-50`
- Test: create `tests/project/test_state_analytics.py` (new file; the existing `tests/project/` may not have a `test_state.py`; use a dedicated file to avoid collisions)

- [ ] **Step 1: Write the failing migration test**

```python
# tests/project/test_state_analytics.py
"""Migration: schema v1 → v2 adds analytics_reports table."""

from __future__ import annotations

import sqlite3

from devrel_origin.project.state import (
    SCHEMA_VERSION,
    get_schema_version,
    init_db,
    open_db,
)


def test_schema_version_is_2():
    assert SCHEMA_VERSION == 2


def test_init_db_creates_analytics_reports_on_fresh_db(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with open_db(db) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_reports'"
        )
        assert cur.fetchone() is not None


def test_init_db_is_idempotent_on_existing_v1_db(tmp_path):
    db = tmp_path / "state.db"
    # Simulate a v1 DB created before this migration
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE schema_meta (version INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.execute("INSERT INTO schema_meta (version, applied_at) VALUES (1, datetime('now'))")
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT, "
            "started_at TEXT, finished_at TEXT, error TEXT)"
        )
        conn.execute(
            "INSERT INTO jobs (id, kind, status) VALUES ('job-1', 'run', 'completed')"
        )
        conn.commit()

    init_db(db)  # apply v2 migration

    assert get_schema_version(db) == 2
    with open_db(db) as conn:
        # Pre-existing data preserved
        rows = conn.execute("SELECT id FROM jobs").fetchall()
        assert [r["id"] for r in rows] == ["job-1"]
        # New table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_reports'"
        )
        assert cur.fetchone() is not None


def test_can_insert_and_read_analytics_report(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            ("2026-04-25T00:00:00Z", "2026-05-02T00:00:00Z", '{"foo": "bar"}'),
        )
        conn.commit()
        row = conn.execute(
            "SELECT report_json FROM analytics_reports WHERE id = 1"
        ).fetchone()
        assert row["report_json"] == '{"foo": "bar"}'
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/project/test_state_analytics.py -v
```

Expected: 4 FAIL — `SCHEMA_VERSION == 1` and the table doesn't exist.

- [ ] **Step 3: Apply the migration**

In `src/devrel_origin/project/state.py`:

Replace `SCHEMA_VERSION = 1` with:

```python
SCHEMA_VERSION = 2
```

Append to the `SCHEMA` triple-quoted block (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS analytics_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analytics_reports_period
    ON analytics_reports(period_end);
```

The existing `init_db()` already runs `executescript(SCHEMA)` which is idempotent because of `IF NOT EXISTS`. The `INSERT OR IGNORE INTO schema_meta` line will not insert the new version because the row with version=1 already exists. Replace it with:

```python
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (version, applied_at) VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )
        conn.commit()
```

(Note: `INSERT OR REPLACE` keeps `schema_meta` to a single current-version row; the existing `get_schema_version` returns `MAX(version)`, so this is safe.)

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/project/test_state_analytics.py -v
pytest tests/project/ -v   # confirm no regression in other state tests
```

Expected: 4 PASSED in `test_state_analytics`, no failures in other `tests/project/` files.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/project/state.py tests/project/test_state_analytics.py
git commit -m "feat(state): schema v2 — analytics_reports table for Argus"
```

---

## Task 4: PostHog collector

**Files:**
- Create: `src/devrel_origin/tools/analytics.py`
- Test: `tests/test_analytics_collectors.py`

- [ ] **Step 1: Write failing test for PostHogCollector**

```python
# tests/test_analytics_collectors.py
"""Tests for Argus's per-source data collectors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_origin.core.argus import PerformanceMetric
from devrel_origin.tools.analytics import PostHogCollector


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_posthog_collector_returns_per_url_pageviews():
    # Mock PostHogClient so we don't hit the network
    fake_client = MagicMock()
    fake_client.fetch_events_by_url = AsyncMock(return_value=[
        {"url": "https://example.com/blog/cli-launch", "title": "CLI launch",
         "page_views": 5400, "unique_visitors": 3200},
        {"url": "https://example.com/blog/python-testing", "title": "Python testing",
         "page_views": 1200, "unique_visitors": 800},
    ])

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
    fake_client.fetch_events_by_url = AsyncMock(return_value=[
        {"url": "https://example.com/", "title": "Home", "page_views": 999,
         "unique_visitors": 500},
        {"url": "https://example.com/pricing", "title": "Pricing",
         "page_views": 444, "unique_visitors": 200},
        {"url": "https://example.com/blog/x", "title": "X",
         "page_views": 100, "unique_visitors": 50},
    ])

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
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_analytics_collectors.py -v
```

Expected: 3 FAIL with `ModuleNotFoundError: No module named 'devrel_origin.tools.analytics'`.

- [ ] **Step 3: Implement PostHogCollector**

```python
# src/devrel_origin/tools/analytics.py
"""Argus data collectors — one class per source.

Each collector exposes a single async method ``collect(period)`` returning
``list[PerformanceMetric]``. Collectors do not raise — failures are logged
and an empty list is returned, so Argus can mark the source unhealthy in
``PerformanceReport.sources_ok`` without aborting the whole report.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from devrel_origin.core.argus import ContentType, PerformanceMetric

if TYPE_CHECKING:
    from devrel_origin.tools.api_client import PostHogClient

logger = logging.getLogger(__name__)

Period = tuple[datetime, datetime]

_LANDING_PATHS = {"/", "/pricing", "/about", "/contact", "/features", "/docs"}


def _classify_url(url: str) -> ContentType:
    """Heuristic: /blog/* → blog, configured landing paths → landing, else blog."""
    from urllib.parse import urlparse

    path = urlparse(url).path or "/"
    if path in _LANDING_PATHS or path.rstrip("/") in {p.rstrip("/") for p in _LANDING_PATHS}:
        return "landing"
    if path.startswith("/blog/"):
        return "blog"
    # Default: anything else under the project domain is treated as a landing page.
    # /blog/* is the canonical "content" path; everything else is a landing/marketing page.
    return "landing"


def _content_id_from_url(url: str) -> str:
    """Stable id: drop scheme+host, strip leading /, replace path with `blog/<slug>` for blog posts."""
    from urllib.parse import urlparse

    path = urlparse(url).path or "/"
    if path.startswith("/blog/"):
        slug = path[len("/blog/"):].rstrip("/")
        return f"blog/{slug}" if slug else "blog/index"
    return path  # landing pages keyed by path verbatim


class PostHogCollector:
    """Pulls page-view + unique-visitor counts from PostHog grouped by URL."""

    def __init__(self, client: "PostHogClient"):
        self.client = client

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        start, end = period
        try:
            rows = await self.client.fetch_events_by_url(start=start, end=end)
        except Exception as exc:  # noqa: BLE001 — collectors swallow source failures
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
                    published_at=end,  # PostHog data is per-period; use period end as anchor
                    primary_metric=float(row.get("page_views", 0) or 0),
                    metric_name="page_views",
                    secondary_metrics={
                        "unique_visitors": float(row.get("unique_visitors", 0) or 0),
                    },
                )
            )
        return metrics
```

Note on the `PostHogClient` API: `fetch_events_by_url(start, end) -> list[dict]` is the contract Argus expects. If `PostHogClient` does not yet have this method, add it as a thin adapter in a follow-up commit (out-of-scope for v1; tests use a `MagicMock`). If the existing client has a different shape, write a one-line bridging method on the collector. Verify before merging by running `grep -n "fetch_events_by_url\|class PostHogClient" src/devrel_origin/tools/api_client.py`.

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_analytics_collectors.py -v -k posthog
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/analytics.py tests/test_analytics_collectors.py
git commit -m "feat(analytics): PostHogCollector — page views per URL"
```

---

## Task 5: GitHub collector

**Files:**
- Modify: `src/devrel_origin/tools/analytics.py`
- Test: `tests/test_analytics_collectors.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_analytics_collectors.py`:

```python
from devrel_origin.tools.analytics import GitHubCollector


@pytest.mark.asyncio
async def test_github_collector_emits_repo_metric():
    fake = MagicMock()
    fake.get_repo_stats = AsyncMock(return_value={
        "stars": 1234,
        "forks": 56,
        "open_issues": 12,
        "stars_delta_7d": 45,    # the period delta — what we score on
        "issues_closed_7d": 8,
    })
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
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_analytics_collectors.py -v -k github
```

Expected: 2 FAIL with `ImportError: cannot import name 'GitHubCollector'`.

- [ ] **Step 3: Implement GitHubCollector**

Append to `src/devrel_origin/tools/analytics.py`:

```python
class GitHubCollector:
    """Emits one PerformanceMetric per repo: stars_delta as primary KPI.

    The wrapped client is expected to expose:
      - ``repo_full_name: str``  (e.g., "openclaw/openclaw")
      - ``async def get_repo_stats() -> dict``  with at minimum
          stars, forks, open_issues, stars_delta_7d, issues_closed_7d.
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
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_analytics_collectors.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/analytics.py tests/test_analytics_collectors.py
git commit -m "feat(analytics): GitHubCollector — stars_delta + repo health"
```

---

## Task 6: Instantly collector

**Files:**
- Modify: `src/devrel_origin/tools/analytics.py`
- Test: `tests/test_analytics_collectors.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_analytics_collectors.py`:

```python
from devrel_origin.tools.analytics import InstantlyCollector


@pytest.mark.asyncio
async def test_instantly_collector_emits_per_campaign_metrics():
    fake = MagicMock()
    fake.list_campaigns_with_analytics = AsyncMock(return_value=[
        {"id": "camp-1", "name": "Q2 outbound", "sent": 1000,
         "opens": 350, "clicks": 80, "replies": 25, "open_rate": 0.35,
         "reply_rate": 0.025},
        {"id": "camp-2", "name": "Founder series", "sent": 500,
         "opens": 100, "clicks": 30, "replies": 50, "open_rate": 0.20,
         "reply_rate": 0.10},
    ])
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
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_analytics_collectors.py -v -k instantly
```

Expected: 2 FAIL with `ImportError: cannot import name 'InstantlyCollector'`.

- [ ] **Step 3: Implement InstantlyCollector**

Append to `src/devrel_origin/tools/analytics.py`:

```python
class InstantlyCollector:
    """One PerformanceMetric per email campaign; reply_rate is primary KPI.

    The wrapped client is expected to expose:
      - ``async def list_campaigns_with_analytics() -> list[dict]``
        with at minimum: id, name, sent, opens, clicks, replies, open_rate, reply_rate.
    """

    def __init__(self, client):
        self.client = client

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        _start, end = period
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
```

If `InstantlyClient.list_campaigns_with_analytics` does not yet exist with this exact signature, add a thin one-method bridging adapter in `instantly_client.py` in a follow-up commit; tests use `MagicMock` so they don't depend on it.

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_analytics_collectors.py -v
```

Expected: 7 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/analytics.py tests/test_analytics_collectors.py
git commit -m "feat(analytics): InstantlyCollector — per-campaign reply_rate + open/click"
```

---

## Task 7: Social collector (reads Echo's `social_mentions` table)

**Files:**
- Modify: `src/devrel_origin/tools/analytics.py`
- Test: `tests/test_analytics_collectors.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_analytics_collectors.py`:

```python
import sqlite3 as _sqlite3

from devrel_origin.tools.analytics import SocialCollector


def _seed_social_mentions_db(db_path):
    """Build a minimal social_mentions table the way Echo writes it.

    The table here mirrors what Echo would write — Argus only reads.
    Including the schema in the test avoids hard-coupling to Echo's
    internal migration timing.
    """
    with _sqlite3.connect(db_path) as conn:
        conn.execute("""
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
        """)
        conn.executemany(
            "INSERT INTO social_mentions "
            "(platform, post_id, title, url, posted_at, upvotes, comments, "
            "engagement_score, is_own_post) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("reddit", "abc1", "Why CLI tools win", "https://reddit.com/r/programming/abc1",
                 "2026-04-30T10:00:00Z", 240, 35, 87.5, 1),
                ("hackernews", "hn-9", "Show HN: devrel-origin", "https://news.ycombinator.com/item?id=9",
                 "2026-04-29T14:00:00Z", 150, 42, 76.0, 1),
                ("reddit", "noise-1", "Random unrelated post", "https://reddit.com/r/x/noise-1",
                 "2026-04-28T08:00:00Z", 5, 1, 6.0, 0),  # not own; collector excludes
            ],
        )
        conn.commit()


@pytest.mark.asyncio
async def test_social_collector_reads_only_own_posts(tmp_path):
    db = tmp_path / "state.db"
    _seed_social_mentions_db(db)
    collector = SocialCollector(db)
    metrics = await collector.collect((
        datetime(2026, 4, 25, tzinfo=timezone.utc),
        datetime(2026, 5, 2, tzinfo=timezone.utc),
    ))
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
    # No tables created — simulates a project where Echo hasn't run yet
    db.touch()
    collector = SocialCollector(db)
    metrics = await collector.collect((_utc_now() - timedelta(days=7), _utc_now()))
    assert metrics == []
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_analytics_collectors.py -v -k social
```

Expected: 2 FAIL with `ImportError: cannot import name 'SocialCollector'`.

- [ ] **Step 3: Implement SocialCollector**

Append to `src/devrel_origin/tools/analytics.py`:

```python
import sqlite3


class SocialCollector:
    """Reads Echo's ``social_mentions`` table, filters to ``is_own_post=1``,
    emits one metric per post with engagement_score as the primary KPI.

    Returns an empty list (and logs) if the table is missing or the period
    yields no rows. Does not raise.
    """

    def __init__(self, state_db_path: Path):
        self.state_db_path = state_db_path

    async def collect(self, period: Period) -> list[PerformanceMetric]:
        start, end = period
        if not self.state_db_path.is_file():
            logger.info("SocialCollector: state.db not present, skipping")
            return []

        try:
            with sqlite3.connect(self.state_db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT platform, post_id, title, url, posted_at, "
                    "upvotes, comments, engagement_score "
                    "FROM social_mentions "
                    "WHERE is_own_post = 1 AND posted_at >= ? AND posted_at <= ?",
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            # Table missing — Echo hasn't created it yet
            logger.info("SocialCollector: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("SocialCollector failed: %s", exc)
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
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_analytics_collectors.py -v
```

Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/tools/analytics.py tests/test_analytics_collectors.py
git commit -m "feat(analytics): SocialCollector — reads Echo's social_mentions for own posts"
```

---

## Task 8: Argus class — orchestration + execute()

**Files:**
- Modify: `src/devrel_origin/core/argus.py`
- Test: `tests/test_argus.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_argus.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from devrel_origin.core.argus import Argus, PerformanceReport


@pytest.mark.asyncio
async def test_argus_run_aggregates_collectors_and_marks_sources_ok():
    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/x", content_type="blog", title="X", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=100.0,
            metric_name="page_views",
        )
    ])
    github = MagicMock()
    github.collect = AsyncMock(side_effect=RuntimeError("boom"))
    instantly = MagicMock()
    instantly.collect = AsyncMock(return_value=[])
    social = MagicMock()
    social.collect = AsyncMock(return_value=[])

    # LLM stub returns a valid recommendations JSON
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=(
        '{"recommendations": [{"action": "investigate", '
        '"target": "blog/x", "target_type": "content", '
        '"rationale": "Only one data point — investigate before deciding.", '
        '"evidence": ["blog/x: 100 views (sole observation)"], '
        '"confidence": 0.5}], '
        '"trend_signals": ["Insufficient corpus for trends"]}'
    ))

    argus = Argus(
        posthog_collector=posthog,
        github_collector=github,
        instantly_collector=instantly,
        social_collector=social,
        llm_client=llm,
        state_db_path=None,  # disable persistence for unit test
    )
    report = await argus.run(
        period_start=_utc(2026, 4, 25),
        period_end=_utc(2026, 5, 2),
    )

    assert isinstance(report, PerformanceReport)
    # PostHog succeeded, GitHub raised inside argus.run (collector is wrapped)
    assert report.sources_ok["posthog"] is True
    assert report.sources_ok["github"] is False
    assert report.sources_ok["instantly"] is True
    assert report.sources_ok["social"] is True
    assert len(report.recommendations) == 1
    assert report.recommendations[0].action == "investigate"


@pytest.mark.asyncio
async def test_argus_run_marks_insufficient_data_when_all_empty():
    empty = MagicMock()
    empty.collect = AsyncMock(return_value=[])
    llm = MagicMock()  # should never be called
    llm.generate = AsyncMock()

    argus = Argus(
        posthog_collector=empty, github_collector=empty,
        instantly_collector=empty, social_collector=empty,
        llm_client=llm, state_db_path=None,
    )
    report = await argus.run(
        period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2),
    )
    assert report.insufficient_data is True
    assert report.recommendations == []
    llm.generate.assert_not_called()
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_argus.py -v -k argus_run
```

Expected: 2 FAIL — `Argus` not yet defined.

- [ ] **Step 3: Implement Argus class shell + run()**

Append to `src/devrel_origin/core/argus.py`:

```python
import asyncio
from pathlib import Path
from typing import Any, Optional


class Argus:
    """Content performance analyst.

    Pulls metrics from four collectors, scores deterministically, and asks
    a Sonnet LLM to generate structured Recommendation objects from the
    ranked leaderboard.

    Each collector failure is isolated — its source is marked False in
    sources_ok and the report continues with degraded coverage.
    """

    def __init__(
        self,
        posthog_collector,
        github_collector,
        instantly_collector,
        social_collector,
        llm_client: Optional[Any] = None,
        state_db_path: Optional[Path] = None,
    ):
        self._collectors = {
            "posthog": posthog_collector,
            "github": github_collector,
            "instantly": instantly_collector,
            "social": social_collector,
        }
        self.llm_client = llm_client
        self.state_db_path = state_db_path

    async def run(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> PerformanceReport:
        """Pull, score, recommend, persist. Returns the PerformanceReport."""
        period = (period_start, period_end)
        all_metrics, sources_ok = await self._gather(period)

        if not all_metrics:
            return PerformanceReport(
                period_start=period_start,
                period_end=period_end,
                top_performers=[], bottom_performers=[],
                trend_signals=[], recommendations=[],
                sources_ok=sources_ok,
                insufficient_data=True,
            )

        # Baselines from past reports for WoW deltas. Empty when no history exists.
        baseline = self._load_baselines() if self.state_db_path else {}
        scored = _score_metrics(all_metrics, baseline_by_type=baseline)

        top, bottom = self._top_bottom(scored)

        recs: list[Recommendation] = []
        trend_signals: list[str] = []
        llm_error: Optional[str] = None
        if self.llm_client:
            try:
                recs, trend_signals = await self._generate_recommendations(scored)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Argus LLM step failed: %s", exc)
                llm_error = str(exc)

        report = PerformanceReport(
            period_start=period_start,
            period_end=period_end,
            top_performers=top,
            bottom_performers=bottom,
            trend_signals=trend_signals,
            recommendations=recs,
            sources_ok=sources_ok,
            llm_error=llm_error,
        )

        if self.state_db_path:
            self._persist(report)

        return report

    async def _gather(
        self, period: tuple[datetime, datetime],
    ) -> tuple[list[PerformanceMetric], dict[str, bool]]:
        """Run all four collectors in parallel; isolate per-source failures."""
        names = list(self._collectors.keys())
        coros = [c.collect(period) for c in self._collectors.values()]
        results = await asyncio.gather(*coros, return_exceptions=True)

        all_metrics: list[PerformanceMetric] = []
        sources_ok: dict[str, bool] = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                sources_ok[name] = False
                logger.warning("Argus collector %s raised: %s", name, result)
            else:
                sources_ok[name] = True
                all_metrics.extend(result)
        return all_metrics, sources_ok

    @staticmethod
    def _top_bottom(
        scored: list[PerformanceMetric],
    ) -> tuple[list[PerformanceMetric], list[PerformanceMetric]]:
        """Top 5 and bottom 3 per content_type, flattened."""
        by_type: dict[str, list[PerformanceMetric]] = {}
        for m in scored:
            by_type.setdefault(m.content_type, []).append(m)
        top: list[PerformanceMetric] = []
        bottom: list[PerformanceMetric] = []
        for group in by_type.values():
            ranked = sorted(group, key=lambda m: m.primary_metric, reverse=True)
            top.extend(ranked[:5])
            bottom.extend(ranked[-3:][::-1])  # reversed so worst is first
        return top, bottom

    def _load_baselines(self) -> dict[str, float]:
        """Stub — populated in Task 10. Returns empty dict for now."""
        return {}

    def _persist(self, report: PerformanceReport) -> None:
        """Stub — populated in Task 10."""
        return

    async def _generate_recommendations(
        self,
        scored: list[PerformanceMetric],
    ) -> tuple[list[Recommendation], list[str]]:
        """Stub — implemented in Task 9."""
        raise NotImplementedError
```

- [ ] **Step 4: Stub `_generate_recommendations` to satisfy this test only**

Replace the `_generate_recommendations` body so the test in this task passes (the real implementation lands in Task 9):

```python
    async def _generate_recommendations(
        self,
        scored: list[PerformanceMetric],
    ) -> tuple[list[Recommendation], list[str]]:
        """Call the LLM once with the scored leaderboard, parse JSON output.

        Implementation completed in Task 9. This stub exists so Task 8 tests
        can exercise the orchestration around the LLM call.
        """
        import json as _json
        from devrel_origin.core.base import strip_markdown_fences

        leaderboard_summary = "\n".join(
            f"- {m.content_id} ({m.content_type}): {m.primary_metric} {m.metric_name}"
            for m in scored[:50]
        )
        raw = await self.llm_client.generate(
            system_prompt="(temporary stub system prompt)",
            user_prompt=f"Leaderboard:\n{leaderboard_summary}",
            temperature=0.2,
            max_tokens=2048,
        )
        cleaned = strip_markdown_fences(raw).strip()
        data = _json.loads(cleaned)
        recs = [
            Recommendation(
                action=r["action"],
                target=r["target"],
                target_type=r["target_type"],
                rationale=r["rationale"],
                evidence=r["evidence"],
                confidence=float(r["confidence"]),
            )
            for r in data.get("recommendations", [])
        ]
        trend_signals = list(data.get("trend_signals", []))
        return recs, trend_signals
```

- [ ] **Step 5: Run tests and verify they pass**

```bash
pytest tests/test_argus.py -v
```

Expected: 9 PASSED (3 schemas + 4 scorer + 2 argus_run).

- [ ] **Step 6: Commit**

```bash
git add src/devrel_origin/core/argus.py tests/test_argus.py
git commit -m "feat(argus): orchestration — collectors -> scorer -> stub recs"
```

---

## Task 9: LLM interpreter — system prompt + structured output

**Files:**
- Modify: `src/devrel_origin/core/argus.py`
- Test: `tests/test_argus.py`

- [ ] **Step 1: Write failing test for prompt content + parser robustness**

Append to `tests/test_argus.py`:

```python
@pytest.mark.asyncio
async def test_argus_prompt_includes_content_type_breakdown_and_action_vocab():
    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/a", content_type="blog", title="A", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=500.0,
            metric_name="page_views",
        ),
        PerformanceMetric(
            content_id="email/c1", content_type="email", title="C1", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=0.05,
            metric_name="reply_rate",
        ),
    ])
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])

    captured_prompts: dict[str, str] = {}

    async def _capture_generate(*, system_prompt, user_prompt, **_):
        captured_prompts["system"] = system_prompt
        captured_prompts["user"] = user_prompt
        return '{"recommendations": [], "trend_signals": []}'

    llm = MagicMock(); llm.generate = AsyncMock(side_effect=_capture_generate)
    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=None)
    await argus.run(period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2))

    sys_prompt = captured_prompts["system"]
    user_prompt = captured_prompts["user"]
    # System prompt names the role and the closed action set
    assert "Argus" in sys_prompt
    for action in ("double_down", "retire", "rewrite", "retest", "amplify", "investigate"):
        assert action in sys_prompt
    # User prompt includes per-type breakdown
    assert "blog" in user_prompt
    assert "email" in user_prompt
    assert "page_views" in user_prompt
    assert "reply_rate" in user_prompt


@pytest.mark.asyncio
async def test_argus_handles_unparseable_llm_output_gracefully():
    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/a", content_type="blog", title="A", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=500.0,
            metric_name="page_views",
        ),
    ])
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.generate = AsyncMock(return_value="this is not json at all")

    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=None)
    report = await argus.run(period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2))

    assert report.recommendations == []
    assert report.llm_error is not None
    # The scoreboard still survived
    assert len(report.top_performers) >= 1
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_argus.py -v -k argus_prompt -k argus_handles
```

Expected: 2 FAIL — current stub doesn't include action vocab, doesn't handle parse errors.

- [ ] **Step 3: Replace `_generate_recommendations` with the real implementation**

In `src/devrel_origin/core/argus.py`, replace the entire `_generate_recommendations` method:

```python
    _DEFAULT_SYSTEM_PROMPT = """You are Argus, a content performance analyst. \
Given a ranked leaderboard of content with engagement metrics, you produce \
structured optimization recommendations.

Your action vocabulary is closed. Use exactly one of:
- double_down: theme/channel is winning; produce more of this kind of content
- retire: content/theme is consistently underperforming; stop investing
- rewrite: specific piece has potential but is poorly executed; redo it
- retest: result is inconclusive; re-run with more samples or a different cohort
- amplify: already-good content is under-distributed; push harder on existing channels
- investigate: anomaly you cannot confidently explain; flag for human review

Be evidence-based. Every recommendation must cite specific metrics with content_ids.
Bias toward fewer, higher-confidence recommendations. Five strong recs beat fifteen weak ones.
Confidence below 0.5 means "investigate" — do not recommend a directional action."""

    @property
    def SYSTEM_PROMPT(self) -> str:
        from devrel_origin.core.base import load_agent_prompt
        return load_agent_prompt(
            "argus", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT,
        )

    async def _generate_recommendations(
        self,
        scored: list[PerformanceMetric],
    ) -> tuple[list[Recommendation], list[str]]:
        """One Sonnet call. Returns (recommendations, trend_signals).

        Bounded input: up to top 10 + bottom 5 per content type, capped at 50 lines.
        Output: JSON with ``recommendations`` and ``trend_signals`` arrays.
        """
        import json as _json
        from devrel_origin.core.base import strip_markdown_fences

        # Build per-type breakdown (top 10 + bottom 5 per type, capped 50 total)
        by_type: dict[str, list[PerformanceMetric]] = {}
        for m in scored:
            by_type.setdefault(m.content_type, []).append(m)

        sections: list[str] = []
        total = 0
        for ctype, group in by_type.items():
            ranked = sorted(group, key=lambda m: m.primary_metric, reverse=True)
            slice_ = ranked[:10] + (ranked[-5:] if len(ranked) > 10 else [])
            section_lines = [f"### {ctype.upper()} ({len(group)} items, primary metric: {ranked[0].metric_name if ranked else 'n/a'})"]
            for m in slice_:
                if total >= 50:
                    break
                pct = f"p{m.percentile:.0f}" if m.percentile is not None else "p?"
                wow = f", wow {m.wow_delta:+.1f}%" if m.wow_delta is not None else ""
                anom = " [ANOMALY]" if m.anomaly_flag else ""
                section_lines.append(
                    f"- {m.content_id}: {m.primary_metric:g} {m.metric_name} ({pct}{wow}){anom} — {m.title}"
                )
                total += 1
            sections.append("\n".join(section_lines))
            if total >= 50:
                break

        leaderboard = "\n\n".join(sections)
        user_prompt = f"""Period leaderboard (top 10 + bottom 5 per content type):

{leaderboard}

Return a JSON object with two top-level keys:
- "recommendations": array of {{action, target, target_type, rationale, evidence, confidence}}
- "trend_signals": array of short strings describing themes/channel patterns (3-7 items)

action ∈ {{double_down, retire, rewrite, retest, amplify, investigate}}
target_type ∈ {{content, theme, channel}}
confidence ∈ [0.0, 1.0]; below 0.5 use action="investigate".

Do not include any commentary outside the JSON."""

        raw = await self.llm_client.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=2048,
        )
        cleaned = strip_markdown_fences(raw).strip()
        data = _json.loads(cleaned)  # raises on bad JSON; caller catches
        recs = [
            Recommendation(
                action=r["action"],
                target=r["target"],
                target_type=r["target_type"],
                rationale=r["rationale"],
                evidence=list(r.get("evidence", [])),
                confidence=float(r["confidence"]),
            )
            for r in data.get("recommendations", [])
        ]
        trend_signals = list(data.get("trend_signals", []))
        return recs, trend_signals
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_argus.py -v
```

Expected: 11 PASSED (9 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/argus.py tests/test_argus.py
git commit -m "feat(argus): LLM interpreter with cached system prompt + closed action vocab"
```

---

## Task 10: Persistence + baselines + markdown rendering

**Files:**
- Modify: `src/devrel_origin/core/argus.py`
- Test: `tests/test_argus.py`

- [ ] **Step 1: Write failing tests for persist + baselines + to_markdown**

Append to `tests/test_argus.py`:

```python
import json as _json

from devrel_origin.project.state import init_db, open_db


@pytest.mark.asyncio
async def test_argus_persists_report_to_state_db(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)

    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/a", content_type="blog", title="A", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=500.0,
            metric_name="page_views",
        )
    ])
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='{"recommendations": [], "trend_signals": []}')

    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=db)
    report = await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))

    with open_db(db) as conn:
        rows = conn.execute(
            "SELECT period_start, period_end, report_json FROM analytics_reports"
        ).fetchall()
    assert len(rows) == 1
    payload = _json.loads(rows[0]["report_json"])
    assert payload["sources_ok"]["posthog"] is True
    assert payload["top_performers"][0]["content_id"] == "blog/a"


@pytest.mark.asyncio
async def test_argus_loads_baselines_from_previous_report(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)

    # Seed a prior report with blog/a at primary_metric=100
    prior = {
        "period_start": "2026-04-18T00:00:00+00:00",
        "period_end": "2026-04-25T00:00:00+00:00",
        "top_performers": [{
            "content_id": "blog/a", "content_type": "blog", "title": "A",
            "url": None, "published_at": "2026-04-23T00:00:00+00:00",
            "primary_metric": 100.0, "metric_name": "page_views",
            "secondary_metrics": {}, "percentile": 100.0,
            "wow_delta": None, "anomaly_flag": False,
        }],
        "bottom_performers": [],
        "trend_signals": [], "recommendations": [],
        "sources_ok": {"posthog": True, "github": True, "instantly": True, "social": True},
        "insufficient_data": False, "llm_error": None,
    }
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) VALUES (?,?,?)",
            (prior["period_start"], prior["period_end"], _json.dumps(prior)),
        )
        conn.commit()

    # Current period: blog/a now at 200
    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/a", content_type="blog", title="A", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=200.0,
            metric_name="page_views",
        )
    ])
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='{"recommendations": [], "trend_signals": []}')

    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=db)
    report = await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))

    a = next(m for m in report.top_performers if m.content_id == "blog/a")
    assert a.wow_delta == pytest.approx(100.0)  # +100% vs baseline


def test_to_markdown_groups_recs_by_action():
    metric = PerformanceMetric(
        content_id="blog/a", content_type="blog", title="A", url=None,
        published_at=_utc(2026, 4, 30), primary_metric=500.0,
        metric_name="page_views", percentile=95.0,
    )
    recs = [
        Recommendation(
            action="double_down", target="theme:python", target_type="theme",
            rationale="Python content rules.", evidence=["blog/a: p95"], confidence=0.9,
        ),
        Recommendation(
            action="retire", target="blog/x", target_type="content",
            rationale="Bottom decile 4 weeks running.", evidence=["blog/x: p5"],
            confidence=0.8,
        ),
    ]
    report = PerformanceReport(
        period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2),
        top_performers=[metric], bottom_performers=[],
        trend_signals=["Python +30% WoW"], recommendations=recs,
        sources_ok={"posthog": True, "github": False, "instantly": True, "social": True},
    )
    md = report.to_markdown()
    assert "Argus Performance Report" in md
    assert "double_down" in md
    assert "retire" in md
    assert "Python +30% WoW" in md
    assert "github: failed" in md  # source health surfaced
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_argus.py -v -k persists -k loads_baselines -k to_markdown
```

Expected: 3 FAIL — `_persist` is a stub, `_load_baselines` returns empty, `to_markdown` not defined.

- [ ] **Step 3: Implement persistence, baseline loading, and markdown rendering**

In `src/devrel_origin/core/argus.py`:

Replace the existing `_persist` and `_load_baselines` stubs with:

```python
    def _load_baselines(self) -> dict[str, float]:
        """Read the most recent prior report from analytics_reports and
        extract per-content-id primary_metric values to use as WoW baselines."""
        if not self.state_db_path or not self.state_db_path.is_file():
            return {}

        try:
            with sqlite3.connect(self.state_db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT report_json FROM analytics_reports "
                    "ORDER BY period_end DESC LIMIT 1"
                ).fetchone()
        except sqlite3.OperationalError:
            return {}

        if not row:
            return {}

        import json as _json
        try:
            data = _json.loads(row["report_json"])
        except _json.JSONDecodeError:
            return {}

        baseline: dict[str, float] = {}
        for section in ("top_performers", "bottom_performers"):
            for entry in data.get(section, []):
                cid = entry.get("content_id")
                if cid:
                    baseline[cid] = float(entry.get("primary_metric", 0.0))
        return baseline

    def _persist(self, report: PerformanceReport) -> None:
        """Serialize the full report to analytics_reports."""
        if not self.state_db_path:
            return
        import json as _json
        with sqlite3.connect(self.state_db_path) as conn:
            conn.execute(
                "INSERT INTO analytics_reports "
                "(period_start, period_end, report_json) VALUES (?, ?, ?)",
                (
                    report.period_start.isoformat(),
                    report.period_end.isoformat(),
                    _json.dumps(_report_to_jsonable(report)),
                ),
            )
            conn.commit()
```

Add `sqlite3` to the existing `import sqlite3` at the top of the file (Task 7 already imported it inside `analytics.py`; for `argus.py` we add a fresh import).

Add the JSON helper and the `to_markdown` method. After the dataclass definitions (and before the `Argus` class), insert:

```python
def _metric_to_jsonable(m: PerformanceMetric) -> dict:
    return {
        "content_id": m.content_id,
        "content_type": m.content_type,
        "title": m.title,
        "url": m.url,
        "published_at": m.published_at.isoformat(),
        "primary_metric": m.primary_metric,
        "metric_name": m.metric_name,
        "secondary_metrics": dict(m.secondary_metrics),
        "percentile": m.percentile,
        "wow_delta": m.wow_delta,
        "anomaly_flag": m.anomaly_flag,
    }


def _rec_to_jsonable(r: Recommendation) -> dict:
    return {
        "action": r.action, "target": r.target, "target_type": r.target_type,
        "rationale": r.rationale, "evidence": list(r.evidence),
        "confidence": r.confidence,
    }


def _report_to_jsonable(r: PerformanceReport) -> dict:
    return {
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "top_performers": [_metric_to_jsonable(m) for m in r.top_performers],
        "bottom_performers": [_metric_to_jsonable(m) for m in r.bottom_performers],
        "trend_signals": list(r.trend_signals),
        "recommendations": [_rec_to_jsonable(rec) for rec in r.recommendations],
        "sources_ok": dict(r.sources_ok),
        "insufficient_data": r.insufficient_data,
        "llm_error": r.llm_error,
    }
```

Add `to_markdown` and `to_json` as methods on `PerformanceReport`. In the dataclass body, add (after the fields):

```python
    def to_json(self) -> dict:
        return _report_to_jsonable(self)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(
            f"# Argus Performance Report — "
            f"{self.period_start.date().isoformat()} to {self.period_end.date().isoformat()}"
        )
        lines.append("")

        # Source health
        lines.append("## Source health")
        for source, ok in self.sources_ok.items():
            lines.append(f"- {source}: {'ok' if ok else 'failed'}")
        if self.llm_error:
            lines.append(f"- llm: failed ({self.llm_error})")
        if self.insufficient_data:
            lines.append("")
            lines.append("> **Insufficient data** — too little signal for trustworthy recommendations.")
        lines.append("")

        # Top performers
        lines.append("## Top performers")
        if not self.top_performers:
            lines.append("_None this period._")
        for m in self.top_performers:
            pct = f"p{m.percentile:.0f}" if m.percentile is not None else "p?"
            lines.append(f"- **{m.content_id}** ({m.content_type}) — {m.primary_metric:g} {m.metric_name} ({pct})")
        lines.append("")

        # Bottom performers
        lines.append("## Bottom performers")
        if not self.bottom_performers:
            lines.append("_None this period._")
        for m in self.bottom_performers:
            pct = f"p{m.percentile:.0f}" if m.percentile is not None else "p?"
            lines.append(f"- **{m.content_id}** ({m.content_type}) — {m.primary_metric:g} {m.metric_name} ({pct})")
        lines.append("")

        # Trend signals
        lines.append("## Trend signals")
        if not self.trend_signals:
            lines.append("_None._")
        for sig in self.trend_signals:
            lines.append(f"- {sig}")
        lines.append("")

        # Recommendations grouped by action
        lines.append("## Recommendations")
        if not self.recommendations:
            lines.append("_No recommendations this period._")
        else:
            grouped: dict[str, list[Recommendation]] = {}
            for r in self.recommendations:
                grouped.setdefault(r.action, []).append(r)
            for action in (
                "double_down", "amplify", "rewrite", "retest", "retire", "investigate",
            ):
                bucket = grouped.get(action, [])
                if not bucket:
                    continue
                lines.append(f"### {action} ({len(bucket)})")
                for r in bucket:
                    lines.append(
                        f"- **{r.target}** (conf {r.confidence:.2f}) — {r.rationale}"
                    )
                    for ev in r.evidence:
                        lines.append(f"  - evidence: {ev}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
```

Add `import sqlite3` at the top of `argus.py` (alongside the existing imports).

- [ ] **Step 4: Run tests and verify they pass**

```bash
pytest tests/test_argus.py -v
```

Expected: 14 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/argus.py tests/test_argus.py
git commit -m "feat(argus): persistence, WoW baselines, markdown rendering"
```

---

## Task 11: CLI verb — `devrel analytics report`

**Files:**
- Create: `src/devrel_origin/cli/analytics.py`
- Test: `tests/cli/test_analytics_command.py`

- [ ] **Step 1: Write failing CLI test**

```python
# tests/cli/test_analytics_command.py
"""Test the `devrel analytics report` CLI verb."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from devrel_origin.cli import app
from devrel_origin.core.argus import PerformanceReport
from devrel_origin.project.state import init_db


runner = CliRunner()


def _stub_report() -> PerformanceReport:
    return PerformanceReport(
        period_start=datetime(2026, 4, 25, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 2, tzinfo=timezone.utc),
        top_performers=[],
        bottom_performers=[],
        trend_signals=["Stub trend"],
        recommendations=[],
        sources_ok={"posthog": True, "github": True, "instantly": True, "social": True},
    )


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    """Bootstrap a minimal .devrel/ in tmp_path and chdir into it."""
    devrel = tmp_path / ".devrel"
    devrel.mkdir()
    (devrel / "deliverables").mkdir()
    init_db(devrel / "state.db")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_analytics_report_writes_markdown_deliverable(project_dir):
    with patch("devrel_origin.cli.analytics._build_argus") as build:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        result = runner.invoke(app, ["analytics", "report", "--since", "7d"])

    assert result.exit_code == 0, result.stdout
    deliverables = list((project_dir / ".devrel" / "deliverables").glob("analytics-*.md"))
    assert len(deliverables) == 1
    assert "Argus Performance Report" in deliverables[0].read_text()


def test_analytics_report_json_format_emits_json(project_dir):
    with patch("devrel_origin.cli.analytics._build_argus") as build:
        argus = build.return_value
        argus.run = AsyncMock(return_value=_stub_report())
        result = runner.invoke(app, ["analytics", "report", "--format", "json"])

    assert result.exit_code == 0
    # The first non-whitespace block of stdout must parse as JSON
    payload = json.loads(result.stdout)
    assert payload["sources_ok"]["posthog"] is True
```

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/cli/test_analytics_command.py -v
```

Expected: 2 FAIL — `analytics` is not a registered verb.

- [ ] **Step 3: Implement the CLI verb**

```python
# src/devrel_origin/cli/analytics.py
"""`devrel analytics report` — Argus performance report.

Pulls the last N days of metrics from PostHog, GitHub, Instantly, and
Echo's social_mentions; ranks deterministically; emits structured
recommendations via a single Sonnet call.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.core.argus import Argus, PerformanceReport

console = Console()

analytics_app = typer.Typer(
    name="analytics",
    help="Content performance analysis (Argus).",
    no_args_is_help=True,
)


_SINCE_RE = re.compile(r"^(\d+)([dwmy])$")


def _parse_since(since: str) -> timedelta:
    """Accept "7d" / "30d" / "12w" / "3m" / "1y"."""
    m = _SINCE_RE.match(since.strip())
    if not m:
        raise typer.BadParameter(f"--since must look like '7d', '30d', '12w': got {since!r}")
    n, unit = int(m.group(1)), m.group(2)
    days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    return timedelta(days=n * days)


def _build_argus(state_db_path: Path) -> Argus:
    """Construct Argus with real collectors. Patched in unit tests."""
    # Lazy imports keep the CLI fast when this verb is not used.
    from devrel_origin.core.llm import LLMClient
    from devrel_origin.tools.analytics import (
        GitHubCollector, InstantlyCollector, PostHogCollector, SocialCollector,
    )
    from devrel_origin.tools.api_client import PostHogClient
    from devrel_origin.tools.github_tools import GitHubTools
    from devrel_origin.tools.instantly_client import InstantlyClient

    posthog = PostHogCollector(PostHogClient())
    github = GitHubCollector(GitHubTools())
    instantly = InstantlyCollector(InstantlyClient())
    social = SocialCollector(state_db_path)
    llm = LLMClient()
    llm.set_agent("argus")
    return Argus(
        posthog_collector=posthog,
        github_collector=github,
        instantly_collector=instantly,
        social_collector=social,
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
    since: str = typer.Option("7d", "--since", help="Lookback window (e.g., 7d, 30d, 12w)."),
    format_: str = typer.Option("md", "--format", help="stdout format: md or json."),
    push: bool = typer.Option(False, "--push", help="Push the report to configured Slack/email."),
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

    out_path = _write_markdown_deliverable(
        report, paths.deliverables if hasattr(paths, "deliverables") else paths.state_db.parent / "deliverables",
    )
    console.print(f"[dim]Wrote deliverable: {out_path}[/dim]")

    if format_ == "json":
        typer.echo(json.dumps(report.to_json(), indent=2, default=str))
    else:
        typer.echo(report.to_markdown())

    if push:
        try:
            from devrel_origin.tools.notifications import NotificationService
            NotificationService.from_env().send_digest(
                subject=f"Argus report — {end.date().isoformat()}",
                body=report.to_markdown(),
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Push failed: {exc}[/yellow]")
```

Note: `find_paths_or_exit` may not expose a `.deliverables` attribute. The fallback `paths.state_db.parent / "deliverables"` resolves to `.devrel/deliverables`, which matches the convention. Verify with `grep -n "deliverables" src/devrel_origin/cli/_common.py src/devrel_origin/project/paths.py` and replace the fallback with the proper attribute if one exists.

- [ ] **Step 4: Wire `analytics_app` into the root CLI**

Edit `src/devrel_origin/cli/__init__.py`:

Add after `from devrel_origin.cli.video import video_app`:

```python
from devrel_origin.cli.analytics import analytics_app
```

Add after `app.add_typer(video_app, name="video")`:

```python
app.add_typer(analytics_app, name="analytics")
```

- [ ] **Step 5: Run tests and verify they pass**

```bash
pytest tests/cli/test_analytics_command.py -v
```

Expected: 2 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_origin/cli/analytics.py src/devrel_origin/cli/__init__.py tests/cli/test_analytics_command.py
git commit -m "feat(cli): devrel analytics report — verb wired into Typer app"
```

---

## Task 12: Atlas integration — optional stage between Sentinel and OKR compilation

**Files:**
- Modify: `src/devrel_origin/core/atlas.py`
- Modify: `src/devrel_origin/core/agent_config.py` (or wherever `AgentConfig` is defined — verify before editing)
- Test: `tests/test_atlas.py`

This task assumes `AgentConfig` already has an `orchestration` section (or similar) with at least one boolean. If it does not, add a minimal `orchestration_analytics_in_run: bool = True` field directly. Verify shape with `grep -n "class AgentConfig\|@dataclass" src/devrel_origin/core/agent_config.py` first.

- [ ] **Step 1: Write failing test**

Append to `tests/test_atlas.py`:

```python
@pytest.mark.asyncio
async def test_atlas_calls_argus_when_analytics_in_run_true(monkeypatch):
    """When config has analytics_in_run=true, Atlas calls Argus.run() once
    after Sentinel and before OKR compilation."""
    from devrel_origin.core.atlas import Atlas

    atlas = _make_atlas_with_minimal_stubs()  # see helper below
    atlas.config.orchestration_analytics_in_run = True

    fake_argus = MagicMock()
    fake_argus.run = AsyncMock(return_value=_stub_argus_report())
    monkeypatch.setattr(atlas, "_build_argus", lambda: fake_argus)

    await atlas.run_weekly_cycle()
    fake_argus.run.assert_called_once()


@pytest.mark.asyncio
async def test_atlas_skips_argus_when_analytics_in_run_false(monkeypatch):
    from devrel_origin.core.atlas import Atlas
    atlas = _make_atlas_with_minimal_stubs()
    atlas.config.orchestration_analytics_in_run = False

    fake_argus = MagicMock()
    fake_argus.run = AsyncMock()
    monkeypatch.setattr(atlas, "_build_argus", lambda: fake_argus)

    await atlas.run_weekly_cycle()
    fake_argus.run.assert_not_called()


@pytest.mark.asyncio
async def test_atlas_continues_when_argus_fails(monkeypatch):
    """A raising Argus should not abort the cycle."""
    from devrel_origin.core.atlas import Atlas
    atlas = _make_atlas_with_minimal_stubs()
    atlas.config.orchestration_analytics_in_run = True

    fake_argus = MagicMock()
    fake_argus.run = AsyncMock(side_effect=RuntimeError("argus down"))
    monkeypatch.setattr(atlas, "_build_argus", lambda: fake_argus)

    # Should not raise
    result = await atlas.run_weekly_cycle()
    assert result is not None  # weekly cycle still produced its top-level result
```

`_make_atlas_with_minimal_stubs` and `_stub_argus_report` are test helpers — copy/paste the equivalents already used in this file for other agent tests. If no such helpers exist, define them inline using `MagicMock()` for every other agent and a `PerformanceReport` with empty lists for the Argus stub.

- [ ] **Step 2: Run tests and verify they fail**

```bash
pytest tests/test_atlas.py -v -k argus
```

Expected: 3 FAIL — `_build_argus` not present, config field missing.

- [ ] **Step 3: Wire the config field**

Verify the existing `AgentConfig` shape:

```bash
grep -n "class AgentConfig\|@dataclass\|orchestration" src/devrel_origin/core/agent_config.py
```

Add a new field. If `AgentConfig` is a dataclass, append to its body:

```python
    orchestration_analytics_in_run: bool = True
```

If `AgentConfig` is loaded from YAML, also wire the YAML key. Read 30 lines around the constructor / loader and add the parse line analogously to other booleans.

- [ ] **Step 4: Wire Argus into Atlas**

In `src/devrel_origin/core/atlas.py`:

Add a top-level import:

```python
from devrel_origin.core.argus import Argus, PerformanceReport
```

Add a `_build_argus(self) -> Argus` method on Atlas that constructs Argus with the same `LLMClient`/state DB Atlas already has (mirror how Sentinel is built — find with `grep -n "Sentinel(" src/devrel_origin/core/atlas.py`).

In `run_weekly_cycle`, immediately after the Sentinel stage completes (find with `grep -n "Sentinel\|sentinel" src/devrel_origin/core/atlas.py`), insert:

```python
        # Stage 5b: Argus content performance analyst
        if getattr(self.config, "orchestration_analytics_in_run", True):
            try:
                argus = self._build_argus()
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=7)
                argus_report = await argus.run(period_start=start, period_end=end)
                ctx.argus_report = argus_report.to_json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Argus stage failed (continuing): %s", exc)
                ctx.argus_report = {"error": str(exc)}
```

Add `argus_report: dict = field(default_factory=dict)` to `SharedContext` (find the dataclass definition and append next to `okr_progress`).

- [ ] **Step 5: Run tests and verify they pass**

```bash
pytest tests/test_atlas.py -v -k argus
pytest tests/test_atlas.py -v   # ensure no regression
```

Expected: 3 PASSED for the new tests, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/devrel_origin/core/atlas.py src/devrel_origin/core/agent_config.py tests/test_atlas.py
git commit -m "feat(atlas): optional Argus stage between Sentinel and OKR compilation"
```

---

## Task 13: Export Argus + final wiring

**Files:**
- Modify: `src/devrel_origin/core/__init__.py`
- Modify: `CLAUDE.md` (single-line update)

- [ ] **Step 1: Add Argus to the core package exports**

Verify current shape, then append the export:

```bash
grep -n "from devrel_origin" src/devrel_origin/core/__init__.py
```

In `src/devrel_origin/core/__init__.py`, add (alongside the existing exports):

```python
from devrel_origin.core.argus import (
    Argus,
    PerformanceMetric,
    PerformanceReport,
    Recommendation,
)
```

And add to the `__all__` list if one exists.

- [ ] **Step 2: Update CLAUDE.md to mention the 13th agent**

In `/Users/macmini/devrel-origin/CLAUDE.md`, find the line:

```
This is **`devrel-origin`**, a `pipx`-installable Python CLI that runs a 12-agent DevRel + Sales + Marketing system against any project repo.
```

Replace `12-agent` with `13-agent`.

In the architecture diagram, under the Health Pipeline section, add a third bullet:

```
│   └── Argus    → Content Performance Analyst (post-publish: PostHog, GitHub,
│                   Instantly, social — structured Recommendation output)
```

In the file map, under `src/devrel_origin/core/`, add:

```
  argus.py      — Content Performance Analyst. PerformanceMetric/Recommendation/
                   PerformanceReport dataclasses, deterministic _score_metrics,
                   single-call Sonnet recommender with closed action vocab.
```

Under `src/devrel_origin/tools/`, add:

```
  analytics.py  — Argus collectors: PostHog, GitHub, Instantly, Social.
                   Each isolates failures (returns []), Argus marks sources_ok.
```

- [ ] **Step 3: Smoke test the whole CLI**

```bash
cd /Users/macmini/devrel-origin && python -c "from devrel_origin.cli import app; print('OK')"
pytest tests/ -q   # full suite — should match prior baseline + new passes
```

Expected: `OK` printed; pytest summary shows the established 744+ pass count plus all the new tests (~24 new passes), with the same 21 baseline failures unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/devrel_origin/core/__init__.py CLAUDE.md
git commit -m "feat(argus): export from core package + CLAUDE.md updated for 13-agent system"
```

---

## Task 14: System prompt file (optional override) + docs polish

**Files:**
- Create: `optimize/argus/system_prompt.txt` (optional, only if you want a checked-in override; agents fall back to the inline default if absent)

This is the only purely-optional task. Skip if you're happy with the inline `_DEFAULT_SYSTEM_PROMPT` from Task 9.

- [ ] **Step 1: Mirror the inline default to a file (optional)**

```bash
mkdir -p /Users/macmini/devrel-origin/optimize/argus
cat > /Users/macmini/devrel-origin/optimize/argus/system_prompt.txt <<'EOF'
You are Argus, a content performance analyst. Given a ranked leaderboard of content with engagement metrics, you produce structured optimization recommendations.

Your action vocabulary is closed. Use exactly one of:
- double_down: theme/channel is winning; produce more of this kind of content
- retire: content/theme is consistently underperforming; stop investing
- rewrite: specific piece has potential but is poorly executed; redo it
- retest: result is inconclusive; re-run with more samples or a different cohort
- amplify: already-good content is under-distributed; push harder on existing channels
- investigate: anomaly you cannot confidently explain; flag for human review

Be evidence-based. Every recommendation must cite specific metrics with content_ids.
Bias toward fewer, higher-confidence recommendations. Five strong recs beat fifteen weak ones.
Confidence below 0.5 means "investigate" — do not recommend a directional action.
EOF
```

- [ ] **Step 2: Verify Argus loads it**

```bash
python -c "
from devrel_origin.core.argus import Argus
from unittest.mock import MagicMock
a = Argus(MagicMock(), MagicMock(), MagicMock(), MagicMock())
print(a.SYSTEM_PROMPT[:120])
"
```

Expected: first 120 chars of the file.

- [ ] **Step 3: Commit (only if Step 1 was done)**

```bash
git add optimize/argus/system_prompt.txt
git commit -m "feat(argus): expose system prompt as optimize/argus/system_prompt.txt override"
```

---

## Self-Review

**Spec coverage:**
- ✅ Schemas (PerformanceMetric / Recommendation / PerformanceReport) → Task 1
- ✅ Deterministic scorer (percentile, WoW, anomaly) → Task 2
- ✅ State DB migration (analytics_reports table) → Task 3
- ✅ 4 collectors (PostHog, GitHub, Instantly, Social) → Tasks 4-7
- ✅ Argus orchestration + per-source isolation → Task 8
- ✅ LLM interpreter with closed action vocab + cached system prompt → Task 9
- ✅ Persistence + WoW baseline lookup + markdown rendering → Task 10
- ✅ `devrel analytics report` CLI verb → Task 11
- ✅ Atlas optional stage gated by `analytics_in_run` config → Task 12
- ✅ Export + CLAUDE.md update → Task 13
- ✅ Optional prompt override file → Task 14

**Placeholder scan:** None remaining — every step has either explicit code, exact bash commands, or a `grep` instruction with a fallback.

**Type consistency:** `_score_metrics` signature stable across Tasks 2/8/10 (`baseline_by_type: dict[str, float]`); `Argus.run` keyword-only `period_start`/`period_end` consistent across Tasks 8/9/10/12; `PerformanceReport.to_markdown` / `to_json` introduced in Task 10 and used in Task 11. Action vocab (`double_down`, `retire`, `rewrite`, `retest`, `amplify`, `investigate`) appears identically in spec, Task 9 system prompt, Task 9 test assertions, and Task 10 markdown grouper.

**Two assumptions surfaced for verification at execution time** (each task instructs the executor to verify before changing the code, so they fail loudly rather than silently):

1. `PostHogClient.fetch_events_by_url(start, end)` and `InstantlyClient.list_campaigns_with_analytics()` may not exist with these exact signatures yet. Tasks 4 and 6 use `MagicMock` so tests pass regardless; the runner verifies the real client method names with `grep` before merging. If a method is missing, add a thin one-method adapter on the client class (out of scope for this plan).
2. `find_paths_or_exit` may or may not expose a `.deliverables` attribute. Task 11 falls back to `paths.state_db.parent / "deliverables"`; the runner verifies with `grep` before merging.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-argus-analytics-agent.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
