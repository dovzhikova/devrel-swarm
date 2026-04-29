"""Tests for Iris feedback synthesizer module."""

import json
from unittest.mock import AsyncMock

import pytest

from devrel_swarm.core.base import strip_markdown_fences
from devrel_swarm.core.iris import (
    DeveloperJourneyStage,
    FeedbackSynthesis,
    FeedbackTheme,
    Iris,
)


@pytest.fixture
def iris(posthog_client, knowledge_base_path):
    return Iris(api_client=posthog_client, knowledge_base_path=knowledge_base_path)


class TestIrisExecute:
    """Test execute() entry point."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, iris):
        result = await iris.execute("Synthesize weekly feedback")
        assert result["agent"] == "iris"
        assert result["status"] == "synthesized"
        assert "themes" in result
        assert "journey_map" in result

    @pytest.mark.asyncio
    async def test_execute_with_sage_context(self, iris):
        context = {
            "sage_triage": {
                "issues": [
                    {"id": "1", "title": "Bug: crash"},
                    {"id": "2", "title": "Feature request"},
                ],
            },
        }
        result = await iris.execute("Synthesize feedback", context=context)
        assert result["upstream_issues_processed"] == 2

    @pytest.mark.asyncio
    async def test_execute_with_empty_context(self, iris):
        result = await iris.execute("Synthesize feedback", context={})
        assert result["upstream_issues_processed"] == 0

    @pytest.mark.asyncio
    async def test_execute_with_none_context(self, iris):
        result = await iris.execute("Synthesize feedback", context=None)
        assert result["upstream_issues_processed"] == 0


class TestIrisJourneyMapping:
    """Test _map_to_journey() stage generation."""

    def test_produces_all_five_stages(self, iris):
        stages = iris._map_to_journey([])
        assert len(stages) == 5
        stage_names = [s.stage for s in stages]
        assert "discovery" in stage_names
        assert "evaluation" in stage_names
        assert "onboarding" in stage_names
        assert "integration" in stage_names
        assert "scaling" in stage_names

    def test_stages_are_developer_journey_stages(self, iris):
        stages = iris._map_to_journey([])
        for stage in stages:
            assert isinstance(stage, DeveloperJourneyStage)
            assert stage.drop_off_risk in ("low", "medium", "high")


class TestIrisRecommendations:
    """Test _generate_recommendations() from themes."""

    def test_generates_recommendations_from_themes(self, iris):
        themes = [
            FeedbackTheme(
                theme_id="t1",
                title="SDK installation friction",
                description="Developers struggle with SDK setup",
                frequency=15,
                severity=7.0,
                composite_score=105.0,
                sources=["github", "discourse"],
                representative_quotes=["Hard to install"],
                product_areas=["sdks"],
                recommended_actions=["Simplify install steps", "Add video tutorial"],
            ),
        ]
        recs = iris._generate_recommendations(themes)
        assert len(recs) == 1
        assert recs[0]["theme"] == "SDK installation friction"
        assert "15 mentions" in recs[0]["evidence"]

    def test_empty_themes_yields_no_recommendations(self, iris):
        recs = iris._generate_recommendations([])
        assert recs == []


class TestIrisContentOpportunities:
    """Test _find_content_opportunities() from themes."""

    def test_finds_content_opportunities(self, iris):
        themes = [
            FeedbackTheme(
                theme_id="t1",
                title="Feature flags setup",
                description="Confusion around feature flags",
                frequency=10,
                severity=6.0,
                composite_score=60.0,
                sources=["github"],
                representative_quotes=[],
                product_areas=["feature_flags"],
                recommended_actions=["Write tutorial"],
            ),
            FeedbackTheme(
                theme_id="t2",
                title="Session replay config",
                description="Hard to configure replay",
                frequency=8,
                severity=8.0,
                composite_score=64.0,
                sources=["discourse"],
                representative_quotes=[],
                product_areas=["session_replay"],
                recommended_actions=["Improve docs"],
            ),
        ]
        opportunities = iris._find_content_opportunities(themes)
        assert len(opportunities) == 2
        # Should be sorted by composite_score descending
        assert "Session replay config" in opportunities[0]

    def test_limits_to_top_five(self, iris):
        themes = [
            FeedbackTheme(
                theme_id=f"t{i}",
                title=f"Theme {i}",
                description="desc",
                frequency=i,
                severity=5.0,
                composite_score=i * 5.0,
                sources=[],
                representative_quotes=[],
                product_areas=[],
                recommended_actions=[],
            )
            for i in range(10)
        ]
        opportunities = iris._find_content_opportunities(themes)
        assert len(opportunities) <= 5


class TestIrisSynthesizeWeekly:
    """Test synthesize_weekly() integration."""

    @pytest.mark.asyncio
    async def test_synthesize_with_all_sources(self, iris):
        result = await iris.synthesize_weekly(
            sage_triage={"issues": [{"id": "1"}, {"id": "2"}]},
            discourse_posts=[{"id": "d1"}],
            support_tickets=[{"id": "s1"}],
        )
        assert isinstance(result, FeedbackSynthesis)
        assert result.total_signals == 4
        assert result.period == "weekly"

    @pytest.mark.asyncio
    async def test_synthesize_with_empty_sources(self, iris):
        result = await iris.synthesize_weekly(sage_triage={})
        assert isinstance(result, FeedbackSynthesis)
        assert result.total_signals == 0


class TestFeedbackThemeDataclass:
    """Test FeedbackTheme composite score semantics."""

    def test_composite_score_is_frequency_times_severity(self):
        theme = FeedbackTheme(
            theme_id="t1",
            title="Test",
            description="Test theme",
            frequency=10,
            severity=7.5,
            composite_score=10 * 7.5,
            sources=[],
            representative_quotes=[],
            product_areas=[],
            recommended_actions=[],
        )
        assert theme.composite_score == 75.0


class TestIrisExecuteWired:
    """Test that execute() extracts themes via LLM."""

    @pytest.fixture
    def llm_response(self):
        """Sample LLM response for theme extraction."""
        return json.dumps(
            {
                "themes": [
                    {
                        "theme_id": "t1",
                        "title": "SDK initialization failures",
                        "description": "Multiple users report SDK crash on startup",
                        "frequency": 3,
                        "severity": 7.0,
                        "sources": ["github"],
                        "representative_quotes": ["Getting crash on startup"],
                        "product_areas": ["sdks"],
                        "recommended_actions": [
                            "Fix React Native SDK init",
                            "Add error boundary docs",
                        ],
                        "journey_stage": "onboarding",
                    },
                    {
                        "theme_id": "t2",
                        "title": "Documentation outdated",
                        "description": "Docs reference old API versions",
                        "frequency": 2,
                        "severity": 4.0,
                        "sources": ["github"],
                        "representative_quotes": ["The docs reference the old API"],
                        "product_areas": ["feature_flags"],
                        "recommended_actions": ["Update feature flags tutorial"],
                        "journey_stage": "evaluation",
                    },
                ]
            }
        )

    @pytest.fixture
    def wired_iris(self, posthog_client, knowledge_base_path, mock_llm_client, llm_response):
        mock_llm_client.generate = AsyncMock(return_value=llm_response)
        return Iris(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.mark.asyncio
    async def test_execute_extracts_themes(self, wired_iris):
        context = {
            "sage_triage": {
                "issues": [
                    {"number": 101, "title": "Bug: SDK init fails", "category": "bug"},
                    {"number": 103, "title": "Docs outdated", "category": "docs"},
                ],
            },
        }
        result = await wired_iris.execute("Synthesize feedback", context=context)
        assert len(result["themes"]) == 2
        assert result["themes"][0]["title"] == "SDK initialization failures"
        assert result["upstream_issues_processed"] == 2

    @pytest.mark.asyncio
    async def test_execute_maps_journey_from_themes(self, wired_iris):
        context = {"sage_triage": {"issues": [{"number": 1, "title": "test"}]}}
        result = await wired_iris.execute("Synthesize", context=context)
        journey = result["journey_map"]
        # onboarding should have pain points from theme t1 (SDK init maps to onboarding via "sdk" keyword)
        assert any(
            stage["friction_score"] > 0 for stage in journey.values() if isinstance(stage, dict)
        )

    @pytest.mark.asyncio
    async def test_execute_without_llm_returns_empty_themes(
        self, posthog_client, knowledge_base_path
    ):
        iris = Iris(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        result = await iris.execute(
            "Synthesize", context={"sage_triage": {"issues": [{"number": 1}]}}
        )
        assert result["themes"] == []

    @pytest.mark.asyncio
    async def test_execute_handles_markdown_fenced_json(
        self, posthog_client, knowledge_base_path, mock_llm_client, llm_response
    ):
        """LLM wraps JSON in ```json ... ``` fences — should still parse."""
        fenced = f"```json\n{llm_response}\n```"
        mock_llm_client.generate = AsyncMock(return_value=fenced)
        iris = Iris(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        context = {"sage_triage": {"issues": [{"number": 1, "title": "test"}]}}
        result = await iris.execute("Synthesize", context=context)
        assert len(result["themes"]) == 2


class TestStripMarkdownFences:
    """Test the strip_markdown_fences helper."""

    def test_strips_json_fence(self):
        assert strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_plain_fence(self):
        assert strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_no_fence_passthrough(self):
        assert strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_strips_whitespace_around_fences(self):
        assert strip_markdown_fences('  ```json\n{"a": 1}\n```  ') == '{"a": 1}'
