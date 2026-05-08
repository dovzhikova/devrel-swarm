"""Unit tests for the Cyra (CRO) agent."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.cyra import (
    CroReport,  # noqa: F401 -- used by later test classes in Tasks 3-9
    Cyra,
    DropOff,
    FunnelStep,
    Hypothesis,
)
from devrel_swarm.core.growth import Pillar, Recommendation, TargetKind
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project import state
from devrel_swarm.tools.api_client import PostHogClient


class TestDataclasses:
    def test_funnel_step_round_trip(self):
        step = FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0)
        assert step.to_dict()["name"] == "$pageview"
        assert FunnelStep.from_dict(step.to_dict()) == step

    def test_dropoff_pp_delta(self):
        d = DropOff(
            from_step="signup_started",
            to_step="signup_completed",
            from_count=300,
            to_count=120,
            conversion_rate=0.4,
            pp_delta_vs_prior=-0.08,
            sample_size=300,
        )
        assert d.absolute_drop == 180
        assert d.is_significant_deterioration is True  # |pp_delta| >= 0.05

    def test_hypothesis_ice_score(self):
        h = Hypothesis(
            title="Add social proof above CTA",
            rationale="Drop-off correlates with low trust signals",
            impact=8,
            confidence=6,
            effort=3,
        )
        # ICE = (impact * confidence) / effort
        assert h.ice_score == pytest.approx(16.0)


class TestFunnelAutodetect:
    @pytest.mark.asyncio
    async def test_picks_top_pageview_to_custom_event_chain(self):
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(
            return_value=[
                ("$pageview", 12500),
                ("signup_started", 3200),
                ("signup_completed", 1850),
                ("first_value", 600),
                ("$identify", 11000),  # PostHog system event - filter out
            ]
        )
        cyra = Cyra(posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"))
        funnel = await cyra._autodetect_funnel(days=7)
        assert funnel[0] == "$pageview"
        assert "$identify" not in funnel  # system events filtered
        assert "signup_started" in funnel
        assert len(funnel) >= 3

    @pytest.mark.asyncio
    async def test_returns_override_when_config_specifies(self):
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(return_value=[])  # would auto-detect to nothing
        cyra = Cyra(
            posthog_client=posthog,
            llm_client=MagicMock(),
            db_path=Path("/tmp/x.db"),
            funnel_override=["$pageview", "signup_started", "signup_completed"],
        )
        funnel = await cyra._autodetect_funnel(days=7)
        assert funnel == ["$pageview", "signup_started", "signup_completed"]
        # event_volumes never called when override is set
        posthog.event_volumes.assert_not_called()


class TestDropoffRanking:
    @pytest.mark.asyncio
    async def test_compute_dropoffs_marks_deterioration(self):
        posthog = MagicMock(spec=PostHogClient)
        # 7d: 1000 -> 300 (30% conv) -> 120 (40% conv from prior step)
        # 14d: 1000 -> 350 (35% conv) -> 150 (43% conv)
        posthog.funnel_query = AsyncMock(
            side_effect=[
                [
                    {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 300, "average_conversion_time": 120},
                    {"name": "signup_completed", "count": 120, "average_conversion_time": 600},
                ],
                [  # 14-day window (prior period)
                    {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 350, "average_conversion_time": 110},
                    {"name": "signup_completed", "count": 150, "average_conversion_time": 580},
                ],
            ]
        )
        cyra = Cyra(posthog_client=posthog, llm_client=MagicMock(), db_path=Path("/tmp/x.db"))
        dropoffs = await cyra._compute_dropoffs(
            funnel=["$pageview", "signup_started", "signup_completed"],
            days=7,
        )
        # Step 0->1: 30% (current) vs 35% (prior) = -5pp deterioration -> significant
        # Step 1->2: 40% (current) vs ~43% (prior) = -3pp -> not significant
        assert dropoffs[0].is_significant_deterioration is True
        assert dropoffs[1].is_significant_deterioration is False
        # Sorted by absolute_drop (largest first); both have absolute_drop=700 vs 180
        assert dropoffs[0].absolute_drop > dropoffs[1].absolute_drop


class TestHypothesisGeneration:
    @pytest.mark.asyncio
    async def test_generate_hypotheses_calls_llm_with_priors(self):
        llm = MagicMock(spec=LLMClient)
        llm.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "hypotheses": [
                        {
                            "title": "Add social proof",
                            "rationale": "Trust signals low",
                            "impact": 8,
                            "confidence": 6,
                            "effort": 1,
                        },
                        {
                            "title": "Reduce form fields",
                            "rationale": "10-field form is friction",
                            "impact": 7,
                            "confidence": 8,
                            "effort": 4,
                        },
                        {
                            "title": "A/B test CTA copy",
                            "rationale": "Generic 'Submit' button",
                            "impact": 5,
                            "confidence": 7,
                            "effort": 5,
                        },
                    ]
                }
            )
        )

        cyra = Cyra(posthog_client=MagicMock(), llm_client=llm, db_path=Path("/tmp/x.db"))
        dropoff = DropOff(
            from_step="signup_started",
            to_step="signup_completed",
            from_count=300,
            to_count=120,
            conversion_rate=0.4,
            pp_delta_vs_prior=-0.08,
            sample_size=300,
        )
        hyps = await cyra._generate_hypotheses(
            dropoff=dropoff,
            page_html="<form><input/><input/></form>",
            iris_themes=["form too long"],
            sage_friction=["users complain about social login"],
        )
        assert len(hyps) == 3
        assert hyps[0].title == "Add social proof"
        # Sorted by ICE score descending
        assert hyps[0].ice_score >= hyps[-1].ice_score
        # LLM was called with priors in the prompt
        prompt_arg = (
            llm.generate.call_args.kwargs.get("user_prompt") or llm.generate.call_args[0][1]
        )
        assert "form too long" in prompt_arg
        assert "social login" in prompt_arg


class TestCohortSplit:
    @pytest.mark.asyncio
    async def test_cohort_split_returns_segment_breakdown(self):
        posthog = MagicMock(spec=PostHogClient)
        # Funnel query returns full + per-segment breakdowns
        posthog.funnel_query = AsyncMock(
            side_effect=[
                # twitter / mobile
                [
                    {"name": "$pageview", "count": 400, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 80, "average_conversion_time": 120},
                ],
                # google / desktop
                [
                    {"name": "$pageview", "count": 600, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 220, "average_conversion_time": 100},
                ],
            ]
        )
        cyra = Cyra(
            posthog_client=posthog,
            llm_client=MagicMock(),
            db_path=Path("/tmp/x.db"),
            min_sample_size=300,
        )
        breakdown = await cyra._cohort_split(
            funnel=["$pageview", "signup_started"],
            segments=[("twitter", "mobile"), ("google", "desktop")],
            days=7,
        )
        assert "twitter|mobile" in breakdown
        assert breakdown["twitter|mobile"]["conversion_rate"] == pytest.approx(0.20)
        assert breakdown["google|desktop"]["conversion_rate"] == pytest.approx(0.367, abs=0.01)

    @pytest.mark.asyncio
    async def test_cohort_split_skips_below_min_sample(self):
        posthog = MagicMock(spec=PostHogClient)
        # Sample of 100, below default min of 500
        posthog.funnel_query = AsyncMock(
            return_value=[
                {"name": "$pageview", "count": 100, "average_conversion_time": 0},
                {"name": "signup_started", "count": 25, "average_conversion_time": 120},
            ]
        )
        cyra = Cyra(
            posthog_client=posthog,
            llm_client=MagicMock(),
            db_path=Path("/tmp/x.db"),
            min_sample_size=500,
        )
        breakdown = await cyra._cohort_split(
            funnel=["$pageview", "signup_started"],
            segments=[("twitter", "mobile")],
            days=7,
        )
        assert breakdown == {}  # below threshold, suppressed


@pytest.fixture
def init_db(tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    # Seed a minimal report row so persist_recommendation has a valid report_id (FK)
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO analytics_reports (period_start, period_end, report_json) "
            "VALUES (?, ?, ?)",
            ("2026-03-25", "2026-04-01", "{}"),
        )
        report_id = cur.lastrowid
        conn.commit()
    return db, report_id


class TestPersistRecommendations:
    @pytest.mark.asyncio
    async def test_persist_writes_one_row_per_dropoff(self, init_db):
        db, report_id = init_db
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=db)
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="default",
            funnel=[
                FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0),
                FunnelStep(name="signup_started", index=1, count=300, conversion_rate=0.30),
            ],
            dropoffs=[
                DropOff(
                    from_step="$pageview",
                    to_step="signup_started",
                    from_count=1000,
                    to_count=300,
                    conversion_rate=0.30,
                    pp_delta_vs_prior=-0.08,
                    sample_size=1000,
                ),
            ],
            hypotheses_by_step={
                "signup_started": [
                    Hypothesis(
                        title="Add social proof",
                        rationale="Low trust",
                        impact=8,
                        confidence=6,
                        effort=3,
                    ),
                ],
            },
        )
        cyra._persist(report, report_id=report_id)

        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT pillar, action, target, target_kind, source_ids_json "
                "FROM analytics_recommendations WHERE pillar = 'cro'"
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "cro"
        assert rows[0][1] == "retest"  # significant deterioration -> retest
        assert rows[0][2] == "signup_started"
        assert rows[0][3] == "funnel_step"
        # source_ids contains the hypothesis dicts as JSON-serialized strings
        sids = json.loads(rows[0][4])
        assert len(sids) == 1
        assert "Add social proof" in sids[0]

    @pytest.mark.asyncio
    async def test_persist_skips_when_report_id_zero(self, init_db):
        """report_id=0 means _insert_cro_report_row had no real DB; skip persist
        instead of FK-violating against a non-existent analytics_reports row."""
        db, _real_report_id = init_db
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=db)
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="default",
            funnel=[
                FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0),
            ],
            dropoffs=[
                DropOff(
                    from_step="$pageview",
                    to_step="signup_started",
                    from_count=1000,
                    to_count=300,
                    conversion_rate=0.30,
                    pp_delta_vs_prior=-0.08,
                    sample_size=1000,
                ),
            ],
        )
        # report_id=0 is the bug-trap: should silently no-op, not raise FK error.
        cyra._persist(report, report_id=0)

        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM analytics_recommendations WHERE pillar = 'cro'"
            )
            assert cur.fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_persist_skips_when_db_path_unwritable(self, tmp_path):
        """Path("/dev/null") (Atlas's no-project_paths fallback) must skip cleanly."""
        cyra = Cyra(
            posthog_client=MagicMock(),
            llm_client=MagicMock(),
            db_path=Path("/dev/null"),
        )
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="default",
            funnel=[],
            dropoffs=[
                DropOff(
                    from_step="$pageview",
                    to_step="signup_started",
                    from_count=1000,
                    to_count=300,
                    conversion_rate=0.30,
                    pp_delta_vs_prior=-0.08,
                    sample_size=1000,
                ),
            ],
        )
        # Should not raise even though report_id is positive; db_path is the gate.
        cyra._persist(report, report_id=42)


class TestPersistFunnelMetrics:
    @pytest.mark.asyncio
    async def test_writes_one_row_per_funnel_step(self, init_db):
        """devrel cro history queries cro_funnel_metrics; without these inserts
        the trend table stays empty even after Cyra has run."""
        db, _ = init_db
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=db)
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="signup",
            funnel=[
                FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0),
                FunnelStep(name="signup_started", index=1, count=300, conversion_rate=0.30),
                FunnelStep(name="signup_complete", index=2, count=120, conversion_rate=0.12),
            ],
            dropoffs=[],
        )
        cyra._persist_funnel_metrics(report)

        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT funnel_id, step_index, conversion_rate, sample_size "
                "FROM cro_funnel_metrics WHERE period_end = ? ORDER BY step_index",
                ("2026-04-01",),
            )
            rows = cur.fetchall()
        assert len(rows) == 3
        assert rows[0] == ("signup", 0, 1.0, 1000)
        assert rows[1] == ("signup", 1, 0.30, 300)
        assert rows[2] == ("signup", 2, 0.12, 120)

    @pytest.mark.asyncio
    async def test_replaces_on_rerun(self, init_db):
        """INSERT OR REPLACE: same period rerun overwrites stale snapshots."""
        db, _ = init_db
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=db)

        first = CroReport(
            period_end="2026-04-01",
            funnel_id="signup",
            funnel=[FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0)],
            dropoffs=[],
        )
        second = CroReport(
            period_end="2026-04-01",
            funnel_id="signup",
            funnel=[FunnelStep(name="$pageview", index=0, count=1500, conversion_rate=1.0)],
            dropoffs=[],
        )
        cyra._persist_funnel_metrics(first)
        cyra._persist_funnel_metrics(second)

        with sqlite3.connect(db) as conn:
            cur = conn.execute(
                "SELECT sample_size FROM cro_funnel_metrics WHERE period_end = ?",
                ("2026-04-01",),
            )
            assert cur.fetchall() == [(1500,)]

    @pytest.mark.asyncio
    async def test_skips_when_db_path_unwritable(self):
        """No-op cleanly on the Path('/dev/null') fallback."""
        cyra = Cyra(
            posthog_client=MagicMock(),
            llm_client=MagicMock(),
            db_path=Path("/dev/null"),
        )
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="signup",
            funnel=[FunnelStep(name="$pageview", index=0, count=1000, conversion_rate=1.0)],
            dropoffs=[],
        )
        # Should not raise.
        cyra._persist_funnel_metrics(report)

    @pytest.mark.asyncio
    async def test_skips_when_funnel_empty(self, init_db):
        db, _ = init_db
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=db)
        report = CroReport(period_end="2026-04-01", funnel_id="x", funnel=[], dropoffs=[])
        cyra._persist_funnel_metrics(report)
        with sqlite3.connect(db) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM cro_funnel_metrics")
            assert cur.fetchone()[0] == 0


class TestBriefGeneration:
    def test_write_briefs_creates_one_file_per_recommendation(self, tmp_path):
        cyra = Cyra(posthog_client=MagicMock(), llm_client=MagicMock(), db_path=tmp_path / "x.db")
        report = CroReport(
            period_end="2026-04-01",
            funnel_id="default",
            funnel=[],
            dropoffs=[
                DropOff(
                    from_step="$pageview",
                    to_step="signup_started",
                    from_count=1000,
                    to_count=300,
                    conversion_rate=0.30,
                    pp_delta_vs_prior=-0.08,
                    sample_size=1000,
                ),
            ],
            hypotheses_by_step={
                "signup_started": [
                    Hypothesis(
                        title="Add social proof",
                        rationale="Low trust",
                        impact=8,
                        confidence=6,
                        effort=3,
                    ),
                ],
            },
        )
        # Pre-populate recommendations as if _persist already ran
        report.recommendations = [
            Recommendation(
                pillar=Pillar.CRO,
                action="retest",
                target="signup_started",
                target_kind=TargetKind.FUNNEL_STEP,
                confidence=0.6,
                source_ids=[],
                first_seen_period="2026-04-01",
            ),
        ]
        deliverables_dir = tmp_path / "deliverables"
        cyra._write_briefs(report, deliverables_dir)

        files = list(deliverables_dir.glob("cro-brief-*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "signup_started" in content
        assert "Add social proof" in content
        assert "ICE" in content  # rendered in the table


class TestExecuteEndToEnd:
    @pytest.mark.asyncio
    async def test_execute_full_cycle(self, init_db, tmp_path):
        db, report_id = init_db
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(
            return_value=[
                ("$pageview", 12500),
                ("signup_started", 3200),
                ("signup_completed", 1850),
            ]
        )
        posthog.funnel_query = AsyncMock(
            side_effect=[
                # current 7d
                [
                    {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 300, "average_conversion_time": 120},
                    {"name": "signup_completed", "count": 120, "average_conversion_time": 600},
                ],
                # prior 14d
                [
                    {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 350, "average_conversion_time": 110},
                    {"name": "signup_completed", "count": 150, "average_conversion_time": 580},
                ],
            ]
        )

        llm = MagicMock(spec=LLMClient)
        llm.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "hypotheses": [
                        {
                            "title": "Add social proof",
                            "rationale": "Low trust",
                            "impact": 8,
                            "confidence": 6,
                            "effort": 1,
                        },
                    ]
                }
            )
        )

        cyra = Cyra(posthog_client=posthog, llm_client=llm, db_path=db, hypothesis_count=1)
        report = await cyra.execute(
            period_end="2026-04-01",
            report_id=report_id,
            page_html_by_url={},  # no page HTML in this unit test
            iris_themes=[],
            sage_friction=[],
            deliverables_dir=tmp_path / "deliverables",
        )
        assert report.sources_ok is True
        assert len(report.dropoffs) == 2
        assert len(report.recommendations) >= 1
        # At least one brief was written
        briefs = list((tmp_path / "deliverables").glob("cro-brief-*.md"))
        assert len(briefs) >= 1

    @pytest.mark.asyncio
    async def test_execute_funnel_steps_correct_when_largest_drop_midfunnel(
        self, init_db, tmp_path
    ):
        """Regression: funnel_steps must use from_step name, not sort-order index."""
        db, report_id = init_db
        posthog = MagicMock(spec=PostHogClient)
        posthog.event_volumes = AsyncMock(
            return_value=[
                ("$pageview", 12500),
                ("signup_started", 3200),
                ("signup_completed", 1850),
            ]
        )
        # Construct so the LARGER absolute drop is mid-funnel:
        # $pageview -> signup_started: 1000 -> 900 (drop=100)
        # signup_started -> signup_completed: 900 -> 100 (drop=800, the worst)
        posthog.funnel_query = AsyncMock(
            side_effect=[
                [
                    {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 900, "average_conversion_time": 120},
                    {"name": "signup_completed", "count": 100, "average_conversion_time": 600},
                ],
                [
                    {"name": "$pageview", "count": 1000, "average_conversion_time": 0},
                    {"name": "signup_started", "count": 950, "average_conversion_time": 110},
                    {"name": "signup_completed", "count": 200, "average_conversion_time": 580},
                ],
            ]
        )
        llm = MagicMock(spec=LLMClient)
        llm.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "hypotheses": [
                        {
                            "title": "Test hypothesis",
                            "rationale": "Test",
                            "impact": 5,
                            "confidence": 5,
                            "effort": 5,
                        }
                    ]
                }
            )
        )
        cyra = Cyra(posthog_client=posthog, llm_client=llm, db_path=db, hypothesis_count=1)
        report = await cyra.execute(
            period_end="2026-04-01",
            report_id=report_id,
            page_html_by_url={},
            iris_themes=[],
            sage_friction=[],
            deliverables_dir=tmp_path / "deliverables",
        )
        # funnel_steps should follow funnel order, NOT sort order
        # Expected counts: $pageview=1000, signup_started=900, signup_completed=100
        assert len(report.funnel) == 3
        assert report.funnel[0].name == "$pageview"
        assert report.funnel[0].count == 1000
        assert report.funnel[1].name == "signup_started"
        assert report.funnel[1].count == 900  # not 100 (which would be the case if buggy)
        assert report.funnel[2].name == "signup_completed"
        assert report.funnel[2].count == 100
