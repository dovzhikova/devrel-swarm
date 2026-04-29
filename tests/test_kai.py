"""Tests for Kai content creator module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.kai import ContentPiece, Kai
from devrel_swarm.tools.search_tools import SearchTools


@pytest.fixture
def kai(posthog_client, knowledge_base_path):
    return Kai(api_client=posthog_client, knowledge_base_path=knowledge_base_path)


class TestKaiKnowledgeBase:
    """Test knowledge base search."""

    def test_search_finds_matching_docs(self, kai):
        results = kai.search_knowledge_base("python sdk")
        assert len(results) >= 1
        assert "python" in results[0]["source"].lower()

    def test_search_no_results(self, kai):
        results = kai.search_knowledge_base("nonexistent topic xyz")
        # No keyword matches, but the fallback fills up to max_results
        # from remaining kb docs, so we get results with relevance=0
        assert all(r["relevance"] == 0 for r in results)


class TestKaiExecuteWired:
    """Test that execute() generates content via LLM."""

    @pytest.fixture
    def wired_kai(self, posthog_client, knowledge_base_path, mock_llm_client):
        mock_llm_client.generate = AsyncMock(
            return_value=(
                "# Getting Started with PostHog Feature Flags\n\n"
                "This tutorial walks you through setting up feature flags...\n\n"
                "## Prerequisites\n- PostHog account\n- JavaScript SDK installed\n\n"
                "## Step 1: Create a flag\n```javascript\nposthog.isFeatureEnabled('new-ui')\n```\n"
            )
        )
        return Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )

    @pytest.mark.asyncio
    async def test_execute_generates_content(self, wired_kai, mock_llm_client):
        result = await wired_kai.execute("Write a tutorial on feature flags")
        assert result["status"] == "generated"
        assert "content" in result
        assert len(result["content"]) > 100
        mock_llm_client.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_includes_grounding_sources(self, wired_kai):
        result = await wired_kai.execute("Write about analytics tracking")
        assert "grounding_sources" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm_returns_prompt(self, kai):
        result = await kai.execute("Write a tutorial")
        assert result["status"] == "generated"
        assert "content" not in result
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_uses_upstream_themes(self, wired_kai):
        context = {
            "iris_themes": {
                "themes": [
                    {"title": "SDK init pain", "severity": 7.0},
                ],
            },
        }
        result = await wired_kai.execute("Write tutorial", context=context)
        assert len(result.get("pain_points_addressed", [])) >= 1


class TestKaiOfficialDocsValidation:
    """Test that Kai consults official docs when search_tools is provided."""

    @pytest.mark.asyncio
    async def test_execute_fetches_official_docs(self, posthog_client, knowledge_base_path):
        mock_search = MagicMock(spec=SearchTools)
        mock_search.fetch_official_docs = AsyncMock(
            return_value="## Feature Flags\nOfficial docs on feature flags."
        )
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search,
        )
        result = await kai.execute("Write about feature flags")
        mock_search.fetch_official_docs.assert_awaited_once()
        # The prompt should contain the official docs
        assert "prompt_used" in result
        assert "Official Documentation Reference" in result["prompt_used"]

    @pytest.mark.asyncio
    async def test_execute_without_search_tools_still_works(self, kai):
        result = await kai.execute("Write about feature flags")
        assert result["status"] == "generated"
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_handles_docs_fetch_failure(self, posthog_client, knowledge_base_path):
        mock_search = MagicMock(spec=SearchTools)
        mock_search.fetch_official_docs = AsyncMock(side_effect=Exception("Network error"))
        kai = Kai(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search,
        )
        result = await kai.execute("Write about feature flags")
        # Should not crash — degrades gracefully
        assert result["status"] == "generated"


class TestKaiWriteTutorial:
    """Test write_tutorial() convenience method."""

    @pytest.mark.asyncio
    async def test_write_tutorial_returns_content_piece(self, kai):
        result = await kai.write_tutorial("Setting up PostHog")
        assert isinstance(result, ContentPiece)
        assert result.content_type == "tutorial"
