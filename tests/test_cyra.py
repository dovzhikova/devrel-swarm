"""Unit tests for the Cyra (CRO) agent."""

import json
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
from devrel_swarm.core.llm import LLMClient
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
