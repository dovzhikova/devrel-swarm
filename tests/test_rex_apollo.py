"""Tests for Rex Apollo.io integration."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.rex import Rex
from devrel_swarm.tools.apollo_client import ApolloOrganization


@pytest.fixture
def mock_apollo():
    client = MagicMock()
    client.enrich_organization = AsyncMock(return_value=ApolloOrganization(
        id="org_001", name="Competitor Inc", domain="competitor.io",
        industry="Software", estimated_headcount=500,
        tech_stack=["React", "PostgreSQL"],
        funding_stage="Series B", funding_total=45_000_000.0,
        description="A competitor", linkedin_url="https://linkedin.com/company/comp",
    ))
    client.close = AsyncMock()
    return client


@pytest.fixture
def rex_with_apollo(posthog_client, knowledge_base_path, mock_llm_client, mock_apollo):
    return Rex(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        apollo_client=mock_apollo,
    )


@pytest.fixture
def rex_without_apollo(posthog_client, knowledge_base_path, mock_llm_client):
    return Rex(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
    )


class TestEnrichCompetitorProfile:
    @pytest.mark.asyncio
    async def test_enrichment_returns_dict(self, rex_with_apollo, mock_apollo):
        result = await rex_with_apollo.enrich_competitor_profile(
            "Competitor Inc", "competitor.io",
        )
        assert result is not None
        assert result["name"] == "Competitor Inc"
        assert result["tech_stack"] == ["React", "PostgreSQL"]
        assert result["estimated_headcount"] == 500
        assert result["funding_stage"] == "Series B"
        mock_apollo.enrich_organization.assert_called_once_with(domain="competitor.io")

    @pytest.mark.asyncio
    async def test_enrichment_not_found(self, rex_with_apollo, mock_apollo):
        mock_apollo.enrich_organization = AsyncMock(return_value=None)
        result = await rex_with_apollo.enrich_competitor_profile(
            "Unknown Co", "unknown.xyz",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_enrichment_no_apollo_client(self, rex_without_apollo):
        result = await rex_without_apollo.enrich_competitor_profile(
            "Competitor Inc", "competitor.io",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_enrichment_api_error_handled(self, rex_with_apollo, mock_apollo):
        mock_apollo.enrich_organization = AsyncMock(side_effect=Exception("API down"))
        result = await rex_with_apollo.enrich_competitor_profile(
            "Competitor Inc", "competitor.io",
        )
        assert result is None


class TestRexExecuteWithApollo:
    @pytest.mark.asyncio
    async def test_execute_includes_enriched_profiles(
        self, rex_with_apollo, mock_llm_client,
    ):
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "summary": "Competitive landscape",
            "competitors": [{"name": "Competitor Inc"}],
            "threats": [],
            "opportunities": [],
        }))
        result = await rex_with_apollo.execute(
            "Analyze competitive landscape for: Competitor Inc",
        )
        assert result["status"] == "generated"
        assert "enriched_profiles" in result
        assert len(result["enriched_profiles"]) >= 1

    @pytest.mark.asyncio
    async def test_execute_without_apollo_has_empty_enriched(
        self, rex_without_apollo, mock_llm_client,
    ):
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "summary": "Report",
            "competitors": [],
            "threats": [],
            "opportunities": [],
        }))
        result = await rex_without_apollo.execute(
            "Analyze competitive landscape for: SomeCompetitor",
        )
        assert result.get("enriched_profiles") == []
