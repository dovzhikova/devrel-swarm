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
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

logger = logging.getLogger(__name__)

_ANOMALY_Z_THRESHOLD = 2.5

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


def _score_metrics(
    metrics: list[PerformanceMetric],
    *,
    baseline_by_type: dict[str, float],
) -> list[PerformanceMetric]:
    """Annotate each metric with percentile, wow_delta, and anomaly_flag.

    Pure function — input metrics are not mutated; new instances are returned.

    - percentile: rank within same content_type peers (0..100, 100 = best)
    - wow_delta: % change vs baseline_by_type[content_id], None if no baseline
    - anomaly_flag: |z-score| > _ANOMALY_Z_THRESHOLD against group mean/stdev
    """
    by_type: dict[str, list[PerformanceMetric]] = {}
    for m in metrics:
        by_type.setdefault(m.content_type, []).append(m)

    out: list[PerformanceMetric] = []
    for group in by_type.values():
        values = [m.primary_metric for m in group]
        n = len(values)
        mean = statistics.fmean(values) if values else 0.0
        stdev = statistics.pstdev(values) if n > 1 else 0.0

        for m in group:
            if n <= 1:
                pct = 100.0
            else:
                lower = sum(1 for v in values if v < m.primary_metric)
                pct = (lower / (n - 1)) * 100.0

            baseline = baseline_by_type.get(m.content_id)
            if baseline is None or baseline == 0:
                wow = None
            else:
                wow = ((m.primary_metric - baseline) / baseline) * 100.0

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
