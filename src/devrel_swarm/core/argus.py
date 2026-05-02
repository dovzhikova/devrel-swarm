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
