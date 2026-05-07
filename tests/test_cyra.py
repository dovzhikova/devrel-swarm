"""Unit tests for the Cyra (CRO) agent."""

import json  # noqa: F401 -- used by later test classes in Tasks 3-9
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
from devrel_swarm.core.llm import LLMClient  # noqa: F401 -- used as spec in later test classes
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
