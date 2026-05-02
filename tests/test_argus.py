"""Unit tests for Argus content performance analyst agent."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from devrel_swarm.core.argus import (
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
