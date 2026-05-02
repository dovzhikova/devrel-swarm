"""Unit tests for Argus content performance analyst agent."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from devrel_swarm.core.argus import (
    PerformanceMetric,
    PerformanceReport,
    Recommendation,
    _score_metrics,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


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


def test_scorer_assigns_percentile_within_content_type():
    metrics = [
        _metric("blog/a", "blog", 10.0),
        _metric("blog/b", "blog", 50.0),
        _metric("blog/c", "blog", 90.0),
    ]
    scored = _score_metrics(metrics, baseline_by_type={})
    by_id = {m.content_id: m for m in scored}
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
    assert by_id["blog/a"].percentile == pytest.approx(100.0, abs=1.0)
    assert by_id["email/x"].percentile == pytest.approx(100.0, abs=1.0)


def test_scorer_flags_anomaly_when_zscore_high():
    metrics = [_metric(f"blog/{i}", "blog", 10.0) for i in range(10)]
    metrics.append(_metric("blog/spike", "blog", 1000.0))
    scored = _score_metrics(metrics, baseline_by_type={})
    spike = next(m for m in scored if m.content_id == "blog/spike")
    assert spike.anomaly_flag is True


def test_scorer_computes_wow_delta_against_baseline():
    metrics = [_metric("blog/a", "blog", 200.0)]
    scored = _score_metrics(
        metrics,
        baseline_by_type={"blog/a": 100.0},
    )
    a = scored[0]
    assert a.wow_delta == pytest.approx(100.0)


# ───────────────────────── Argus orchestration ─────────────────────────


from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from devrel_swarm.core.argus import Argus  # noqa: E402


@pytest.mark.asyncio
async def test_argus_run_aggregates_collectors_and_marks_sources_ok():
    posthog = MagicMock()
    posthog.collect = AsyncMock(
        return_value=[
            PerformanceMetric(
                content_id="blog/x", content_type="blog", title="X", url=None,
                published_at=_utc(2026, 4, 30), primary_metric=100.0,
                metric_name="page_views",
            )
        ]
    )
    github = MagicMock()
    github.collect = AsyncMock(side_effect=RuntimeError("boom"))
    instantly = MagicMock()
    instantly.collect = AsyncMock(return_value=[])
    social = MagicMock()
    social.collect = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=(
            '{"recommendations": [{"action": "investigate", '
            '"target": "blog/x", "target_type": "content", '
            '"rationale": "Only one data point.", '
            '"evidence": ["blog/x: 100 views"], '
            '"confidence": 0.5}], '
            '"trend_signals": ["Insufficient corpus"]}'
        )
    )

    argus = Argus(
        posthog_collector=posthog,
        github_collector=github,
        instantly_collector=instantly,
        social_collector=social,
        llm_client=llm,
        state_db_path=None,
    )
    report = await argus.run(
        period_start=_utc(2026, 4, 25),
        period_end=_utc(2026, 5, 2),
    )

    assert isinstance(report, PerformanceReport)
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
    llm = MagicMock()
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


# ───────────────── LLM interpreter (Task 9) ─────────────────


@pytest.mark.asyncio
async def test_argus_prompt_includes_content_type_breakdown_and_action_vocab():
    posthog = MagicMock()
    posthog.collect = AsyncMock(
        return_value=[
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
        ]
    )
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])

    captured: dict[str, str] = {}

    async def _capture_generate(*, system_prompt, user_prompt, **_):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return '{"recommendations": [], "trend_signals": []}'

    llm = MagicMock(); llm.generate = AsyncMock(side_effect=_capture_generate)
    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=None)
    await argus.run(period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2))

    sys_prompt = captured["system"]
    user_prompt = captured["user"]
    assert "Argus" in sys_prompt
    for action in (
        "double_down", "retire", "rewrite", "retest", "amplify", "investigate",
    ):
        assert action in sys_prompt
    assert "blog" in user_prompt
    assert "email" in user_prompt
    assert "page_views" in user_prompt
    assert "reply_rate" in user_prompt


@pytest.mark.asyncio
async def test_argus_handles_unparseable_llm_output_gracefully():
    posthog = MagicMock()
    posthog.collect = AsyncMock(
        return_value=[
            PerformanceMetric(
                content_id="blog/a", content_type="blog", title="A", url=None,
                published_at=_utc(2026, 4, 30), primary_metric=500.0,
                metric_name="page_views",
            ),
        ]
    )
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.generate = AsyncMock(return_value="this is not json at all")

    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=None)
    report = await argus.run(period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2))

    assert report.recommendations == []
    assert report.llm_error is not None
    assert len(report.top_performers) >= 1
