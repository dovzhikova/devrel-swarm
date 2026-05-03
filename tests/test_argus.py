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
    # source_ids defaults to empty list (v2 routing field, optional in v1)
    assert d["source_ids"] == []


def test_recommendation_carries_source_ids():
    r = Recommendation(
        action="rewrite",
        target="blog/cli-launch",
        target_type="content",
        rationale="Bottom decile but anomalously low for the topic.",
        evidence=["blog/cli-launch: 30 views (p4)"],
        confidence=0.7,
        source_ids=["blog/cli-launch"],
    )
    assert r.source_ids == ["blog/cli-launch"]


@pytest.mark.asyncio
async def test_argus_propagates_source_ids_from_llm_to_report():
    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/x", content_type="blog", title="X", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=100.0,
            metric_name="page_views",
        )
    ])
    empty_c = MagicMock(); empty_c.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=(
        '{"recommendations": [{"action": "rewrite", "target": "blog/x", '
        '"target_type": "content", "rationale": "weak hero", '
        '"evidence": ["blog/x: p20"], "confidence": 0.8, '
        '"source_ids": ["blog/x"]}], "trend_signals": []}'
    ))

    argus = Argus(posthog, empty_c, empty_c, empty_c, llm_client=llm, state_db_path=None)
    report = await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))
    assert report.recommendations[0].source_ids == ["blog/x"]
    # JSON serialization includes source_ids
    assert report.to_json()["recommendations"][0]["source_ids"] == ["blog/x"]


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
    scored = _score_metrics(metrics, baseline_by_id={})
    by_id = {m.content_id: m for m in scored}
    assert by_id["blog/c"].percentile == pytest.approx(100.0, abs=1.0)
    assert by_id["blog/a"].percentile == pytest.approx(0.0, abs=1.0)
    assert 30.0 < by_id["blog/b"].percentile < 70.0


def test_scorer_keeps_content_types_independent():
    metrics = [
        _metric("blog/a", "blog", 100.0),
        _metric("email/x", "email", 5.0),
    ]
    scored = _score_metrics(metrics, baseline_by_id={})
    by_id = {m.content_id: m for m in scored}
    assert by_id["blog/a"].percentile == pytest.approx(100.0, abs=1.0)
    assert by_id["email/x"].percentile == pytest.approx(100.0, abs=1.0)


def test_scorer_flags_anomaly_when_zscore_high():
    metrics = [_metric(f"blog/{i}", "blog", 10.0) for i in range(10)]
    metrics.append(_metric("blog/spike", "blog", 1000.0))
    scored = _score_metrics(metrics, baseline_by_id={})
    spike = next(m for m in scored if m.content_id == "blog/spike")
    assert spike.anomaly_flag is True


def test_scorer_computes_wow_delta_against_baseline():
    metrics = [_metric("blog/a", "blog", 200.0)]
    scored = _score_metrics(
        metrics,
        baseline_by_id={"blog/a": 100.0},
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
async def test_argus_prompt_surfaces_truncation_when_over_50_items():
    """When total > 50 LLM-prompt lines, both partial-section omits and
    fully-dropped content types must be flagged in the prompt so the LLM
    knows context is incomplete."""
    # 4 content types of 15 items each = 60 slice items > 50 cap.
    # Plus a 5th tiny type that should get fully dropped.
    metrics = []
    for ctype in ("blog", "landing", "social", "email"):
        for i in range(15):
            metrics.append(PerformanceMetric(
                content_id=f"{ctype}/{i}", content_type=ctype,  # type: ignore[arg-type]
                title=f"{ctype}-{i}", url=None,
                published_at=_utc(2026, 4, 30), primary_metric=float(100 - i),
                metric_name="page_views",
            ))
    # Tiny 5th type that should get fully dropped
    metrics.append(PerformanceMetric(
        content_id="repo/devrel-swarm", content_type="repo", title="repo", url=None,
        published_at=_utc(2026, 4, 30), primary_metric=42.0, metric_name="stars_delta",
    ))

    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=metrics)
    empty_c = MagicMock(); empty_c.collect = AsyncMock(return_value=[])
    captured: dict[str, str] = {}

    async def _capture(*, system_prompt, user_prompt, **_):
        captured["user"] = user_prompt
        return '{"recommendations": [], "trend_signals": []}'

    llm = MagicMock(); llm.generate = AsyncMock(side_effect=_capture)
    argus = Argus(posthog, empty_c, empty_c, empty_c, llm_client=llm, state_db_path=None)
    await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))

    user_prompt = captured["user"]
    # Two truncation paths must surface:
    # 1. Some section was partially truncated — "more ... omitted from this section"
    # 2. The repo type with 1 item never made it in — listed under TRUNCATED
    assert "omitted from this section" in user_prompt
    assert "TRUNCATED" in user_prompt
    assert "repo" in user_prompt.lower()


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


# ───────────────── Persistence + markdown (Task 10) ─────────────────


import json as _json  # noqa: E402

from devrel_swarm.project.state import init_db, open_db  # noqa: E402


@pytest.mark.asyncio
async def test_argus_persists_report_to_state_db(tmp_path):
    db = tmp_path / "state.db"
    init_db(db)

    posthog = MagicMock()
    posthog.collect = AsyncMock(
        return_value=[
            PerformanceMetric(
                content_id="blog/a", content_type="blog", title="A", url=None,
                published_at=_utc(2026, 4, 30), primary_metric=500.0,
                metric_name="page_views",
            )
        ]
    )
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='{"recommendations": [], "trend_signals": []}')

    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=db)
    report = await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))
    assert report is not None  # silence unused

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

    prior = {
        "period_start": "2026-04-18T00:00:00+00:00",
        "period_end": "2026-04-25T00:00:00+00:00",
        "top_performers": [
            {
                "content_id": "blog/a", "content_type": "blog", "title": "A",
                "url": None, "published_at": "2026-04-23T00:00:00+00:00",
                "primary_metric": 100.0, "metric_name": "page_views",
                "secondary_metrics": {}, "percentile": 100.0,
                "wow_delta": None, "anomaly_flag": False,
            }
        ],
        "bottom_performers": [],
        "trend_signals": [], "recommendations": [],
        "sources_ok": {"posthog": True, "github": True, "instantly": True, "social": True},
        "insufficient_data": False, "llm_error": None,
    }
    with open_db(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?,?,?)",
            (prior["period_start"], prior["period_end"], _json.dumps(prior)),
        )
        conn.commit()

    posthog = MagicMock()
    posthog.collect = AsyncMock(
        return_value=[
            PerformanceMetric(
                content_id="blog/a", content_type="blog", title="A", url=None,
                published_at=_utc(2026, 4, 30), primary_metric=200.0,
                metric_name="page_views",
            )
        ]
    )
    empty = MagicMock(); empty.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='{"recommendations": [], "trend_signals": []}')

    argus = Argus(posthog, empty, empty, empty, llm_client=llm, state_db_path=db)
    report = await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))

    a = next(m for m in report.top_performers if m.content_id == "blog/a")
    assert a.wow_delta == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_argus_persists_recommendations_to_analytics_recommendations_table(tmp_path):
    """Persisting a report writes one row per Recommendation into the
    analytics_recommendations table, with applied_at NULL (v2 routing bus)."""
    db = tmp_path / "state.db"
    init_db(db)

    posthog = MagicMock()
    posthog.collect = AsyncMock(return_value=[
        PerformanceMetric(
            content_id="blog/x", content_type="blog", title="X", url=None,
            published_at=_utc(2026, 4, 30), primary_metric=100.0,
            metric_name="page_views",
        )
    ])
    empty_c = MagicMock(); empty_c.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=(
        '{"recommendations": ['
        '{"action": "rewrite", "target": "blog/x", "target_type": "content", '
        '"rationale": "weak hero", "evidence": ["blog/x: p20"], '
        '"confidence": 0.8, "source_ids": ["blog/x"]},'
        '{"action": "double_down", "target": "theme:python", '
        '"target_type": "theme", "rationale": "trending up", '
        '"evidence": ["blog/y: p95"], "confidence": 0.9, "source_ids": ["blog/y"]}'
        '], "trend_signals": []}'
    ))

    argus = Argus(posthog, empty_c, empty_c, empty_c, llm_client=llm, state_db_path=db)
    await argus.run(_utc(2026, 4, 25), _utc(2026, 5, 2))

    with open_db(db) as conn:
        rows = conn.execute(
            "SELECT action, target, source_ids_json, applied_at, first_seen_period "
            "FROM analytics_recommendations ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["action"] == "rewrite"
    assert rows[0]["target"] == "blog/x"
    assert rows[0]["applied_at"] is None  # v1 leaves it NULL
    assert rows[0]["first_seen_period"] == "2026-05-02T00:00:00+00:00"
    import json as _j
    assert _j.loads(rows[0]["source_ids_json"]) == ["blog/x"]
    assert rows[1]["action"] == "double_down"


@pytest.mark.asyncio
async def test_argus_two_runs_use_metric_history_for_wow(tmp_path):
    """End-to-end: first run persists metric_history; second run's WoW
    delta comes from the indexed table, not the legacy JSON blob fallback."""
    db = tmp_path / "state.db"
    init_db(db)

    posthog_run1 = MagicMock()
    posthog_run1.collect = AsyncMock(
        return_value=[
            PerformanceMetric(
                content_id="blog/a", content_type="blog", title="A", url=None,
                published_at=_utc(2026, 4, 25), primary_metric=100.0,
                metric_name="page_views",
            )
        ]
    )
    empty_c = MagicMock(); empty_c.collect = AsyncMock(return_value=[])
    llm = MagicMock()
    llm.generate = AsyncMock(return_value='{"recommendations": [], "trend_signals": []}')

    argus1 = Argus(posthog_run1, empty_c, empty_c, empty_c, llm_client=llm, state_db_path=db)
    await argus1.run(_utc(2026, 4, 18), _utc(2026, 4, 25))

    with open_db(db) as conn:
        rows = conn.execute(
            "SELECT content_id, primary_metric FROM metric_history"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["content_id"] == "blog/a"
    assert rows[0]["primary_metric"] == 100.0

    posthog_run2 = MagicMock()
    posthog_run2.collect = AsyncMock(
        return_value=[
            PerformanceMetric(
                content_id="blog/a", content_type="blog", title="A", url=None,
                published_at=_utc(2026, 5, 2), primary_metric=200.0,
                metric_name="page_views",
            )
        ]
    )
    argus2 = Argus(posthog_run2, empty_c, empty_c, empty_c, llm_client=llm, state_db_path=db)
    report = await argus2.run(_utc(2026, 4, 25), _utc(2026, 5, 2))

    a = next(m for m in report.top_performers if m.content_id == "blog/a")
    assert a.wow_delta == pytest.approx(100.0)


def test_write_recommendation_briefs_skips_non_actionable(tmp_path):
    """Briefs are only written for double_down / amplify / rewrite —
    retire/investigate/retest do not become content tasks."""
    from devrel_swarm.core.argus import write_recommendation_briefs

    report = PerformanceReport(
        period_start=_utc(2026, 4, 25), period_end=_utc(2026, 5, 2),
        top_performers=[], bottom_performers=[],
        trend_signals=[], sources_ok={"posthog": True},
        recommendations=[
            Recommendation(
                action="double_down", target="theme:python", target_type="theme",
                rationale="3x baseline", evidence=["blog/a: p95"],
                confidence=0.9, source_ids=["blog/a", "blog/b"],
            ),
            Recommendation(
                action="retire", target="blog/x", target_type="content",
                rationale="bottom decile 4 weeks", evidence=["blog/x: p5"],
                confidence=0.8, source_ids=["blog/x"],
            ),
            Recommendation(
                action="rewrite", target="blog/y", target_type="content",
                rationale="weak hero", evidence=["blog/y: p20"],
                confidence=0.75, source_ids=["blog/y"],
            ),
        ],
    )
    paths_written = write_recommendation_briefs(report, tmp_path)
    assert len(paths_written) == 2  # double_down + rewrite, NOT retire

    # Brief content includes target + rationale + Next step block
    dd_brief = next(p for p in paths_written if "double_down" in p.name)
    text = dd_brief.read_text()
    assert "theme:python" in text
    assert "3x baseline" in text
    assert "Next step" in text
    assert "devrel content draft" in text  # double_down → draft command


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
    assert "github: failed" in md
