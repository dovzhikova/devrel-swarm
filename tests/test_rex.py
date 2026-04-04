"""Tests for Rex competitive intelligence agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.rex import (
    CompetitorProfile,
    CompetitiveReport,
    MarketPosition,
    Opportunity,
    Rex,
    Threat,
)
from tools.search_tools import SearchResult, SearchTools


@pytest.fixture
def rex(posthog_client, knowledge_base_path):
    """Rex instance without LLM or search tools."""
    return Rex(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        product_name="TestProduct",
    )


@pytest.fixture
def rex_with_llm(posthog_client, knowledge_base_path, mock_llm_client):
    """Rex instance with a mocked LLM client."""
    mock_llm_client.generate = AsyncMock(
        return_value='{"summary":"Test report","competitors":[],'
        '"threats":[],"opportunities":[]}'
    )
    return Rex(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        product_name="TestProduct",
    )


@pytest.fixture
def kb_with_competitors(tmp_path):
    """Knowledge base containing competitor keywords."""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    (kb / "products").mkdir()
    (kb / "products" / "analytics.md").write_text(
        "# Analytics\n\n"
        "Our product vs Mixpanel for event tracking.\n"
        "Amplitude is an alternative to consider.\n"
        "Compared to Heap, we offer better funnels.\n"
    )
    (kb / "sdks").mkdir()
    (kb / "sdks" / "python.md").write_text("# Python SDK\nInstall with pip.")
    return kb


# ======================================================================
# TestRexDataclasses
# ======================================================================


class TestRexDataclasses:
    """Test that all Rex dataclasses instantiate correctly."""

    def test_competitor_profile(self):
        p = CompetitorProfile(
            name="Botpress",
            domain="botpress.com",
            category="chatbot-platform",
            strengths=["visual flow builder", "self-hosted option"],
            weaknesses=["limited LLM support"],
            recent_moves=["launched v13 with GPT integration"],
        )
        assert p.name == "Botpress"
        assert p.domain == "botpress.com"
        assert len(p.strengths) == 2

    def test_market_position(self):
        mp = MarketPosition(
            competitor="Botpress",
            positioning_statement="Open-source chatbot builder",
            differentiators=["visual builder", "on-prem deployment"],
            pricing_tier="freemium",
            target_audience="enterprise IT teams",
        )
        assert mp.competitor == "Botpress"
        assert mp.pricing_tier == "freemium"

    def test_threat(self):
        t = Threat(
            competitor="Rasa",
            threat="Open-source NLU gaining traction",
            severity="high",
        )
        assert t.severity == "high"

    def test_opportunity(self):
        o = Opportunity(
            gap="No competitor offers voice + chat unification",
            recommendation="Emphasize multi-modal",
        )
        assert "voice" in o.gap

    def test_competitive_report(self):
        report = CompetitiveReport(
            profiles=[],
            market_positions=[],
            threats=[],
            opportunities=[],
            recommended_responses=[],
        )
        assert isinstance(report.profiles, list)


# ======================================================================
# TestCompetitorDiscovery
# ======================================================================


class TestCompetitorDiscovery:
    """Test competitor discovery from task strings and KB."""

    def test_discover_from_task_string(self, rex):
        competitors = rex._discover_competitors(
            "Analyze competitive landscape for: Mixpanel, Amplitude, Heap"
        )
        assert "Mixpanel" in competitors
        assert "Amplitude" in competitors
        assert "Heap" in competitors

    def test_discover_from_knowledge_base(self, posthog_client, kb_with_competitors):
        rex = Rex(
            api_client=posthog_client,
            knowledge_base_path=kb_with_competitors,
            product_name="TestProduct",
        )
        competitors = rex._discover_competitors("Analyze the competitive landscape")
        assert "Mixpanel" in competitors
        assert "Amplitude" in competitors
        assert "Heap" in competitors

    def test_discover_deduplicates(self, posthog_client, kb_with_competitors):
        """Competitors found in both task and KB should not be duplicated."""
        rex = Rex(
            api_client=posthog_client,
            knowledge_base_path=kb_with_competitors,
            product_name="TestProduct",
        )
        competitors = rex._discover_competitors(
            "Analyze landscape for: Mixpanel, Amplitude"
        )
        # Mixpanel appears in both task and KB — should only appear once
        assert competitors.count("Mixpanel") == 1


# ======================================================================
# TestUpstreamContext
# ======================================================================


class TestUpstreamContext:
    """Test upstream context extraction from SharedContext."""

    def test_extract_echo_mentions(self, rex):
        context = {
            "echo_social": {
                "top_mentions": [
                    {
                        "platform": "reddit",
                        "title": "Mixpanel vs us",
                        "sentiment": "neutral",
                        "url": "https://reddit.com/r/analytics/1",
                    },
                    {
                        "platform": "hackernews",
                        "title": "Why we switched",
                        "sentiment": "negative",
                        "url": "https://news.ycombinator.com/item?id=1",
                    },
                ],
            },
        }
        extracted = rex._extract_upstream_context(context)
        assert len(extracted["social_mentions"]) == 2
        assert extracted["social_mentions"][0]["platform"] == "reddit"

    def test_extract_sage_issues(self, rex):
        context = {
            "sage_triage": {
                "issues": [
                    {
                        "number": 101,
                        "title": "Feature parity with Amplitude",
                        "category": "feature",
                        "product_area": "analytics",
                    },
                ],
            },
        }
        extracted = rex._extract_upstream_context(context)
        assert len(extracted["community_issues"]) == 1
        assert extracted["community_issues"][0]["number"] == 101

    def test_extract_empty_context(self, rex):
        extracted = rex._extract_upstream_context(None)
        assert extracted["social_mentions"] == []
        assert extracted["community_issues"] == []

        extracted2 = rex._extract_upstream_context({})
        assert extracted2["social_mentions"] == []
        assert extracted2["community_issues"] == []


# ======================================================================
# TestRexExecute
# ======================================================================


class TestRexExecute:
    """Test the execute() method end-to-end."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, rex):
        result = await rex.execute("Analyze competitive landscape")
        assert result["agent"] == "rex"
        assert result["status"] == "generated"
        assert "competitors_discovered" in result
        assert "kb_sources" in result
        assert "prompt_used" in result  # no LLM → falls back to prompt

    @pytest.mark.asyncio
    async def test_execute_without_llm_returns_prompt(self, rex):
        result = await rex.execute("Analyze for: Acme")
        assert "prompt_used" in result
        assert "content" not in result

    @pytest.mark.asyncio
    async def test_execute_finds_competitors(self, rex):
        result = await rex.execute(
            "Competitive analysis for: Mixpanel, Amplitude"
        )
        assert "Mixpanel" in result["competitors_discovered"]
        assert "Amplitude" in result["competitors_discovered"]

    @pytest.mark.asyncio
    async def test_execute_with_upstream_context(self, rex):
        context = {
            "echo_social": {
                "top_mentions": [
                    {
                        "platform": "reddit",
                        "title": "Comparison post",
                        "sentiment": "positive",
                        "url": "https://example.com",
                    },
                ],
            },
            "sage_triage": {
                "issues": [
                    {
                        "number": 42,
                        "title": "Missing feature",
                        "category": "feature",
                        "product_area": "analytics",
                    },
                ],
            },
        }
        result = await rex.execute("Analyze landscape", context=context)
        assert result["upstream_social_mentions"] == 1
        assert result["upstream_community_issues"] == 1

    @pytest.mark.asyncio
    async def test_execute_web_search_failure_graceful(
        self, posthog_client, knowledge_base_path, mock_llm_client,
    ):
        """Web search failure should not crash execute."""
        mock_search = MagicMock(spec=SearchTools)
        mock_search.web_search = AsyncMock(
            side_effect=Exception("Network error")
        )
        mock_llm_client.generate = AsyncMock(
            return_value='{"summary":"ok","competitors":[],'
            '"threats":[],"opportunities":[]}'
        )

        rex = Rex(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            search_tools=mock_search,
            product_name="TestProduct",
        )
        result = await rex.execute("Analyze for: FailCorp")
        assert result["status"] == "generated"
        # Web intel should be empty due to failure but not crash
        assert result["web_intel_sources"].get("FailCorp", 0) == 0

    @pytest.mark.asyncio
    async def test_execute_with_llm_parses_json(self, rex_with_llm):
        result = await rex_with_llm.execute("Analyze landscape")
        assert "content" in result
        assert isinstance(result["content"], dict)
        assert "summary" in result["content"]

    @pytest.mark.asyncio
    async def test_execute_with_search_tools(
        self, posthog_client, knowledge_base_path,
    ):
        """Verify web search results are gathered per competitor."""
        mock_search = MagicMock(spec=SearchTools)
        mock_search.web_search = AsyncMock(
            return_value=[
                SearchResult(
                    title="Mixpanel Review",
                    url="https://example.com/mixpanel",
                    snippet="Mixpanel analytics review",
                    source="web",
                ),
            ]
        )

        rex = Rex(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search,
            product_name="TestProduct",
        )
        result = await rex.execute("Analyze for: Mixpanel")
        assert result["web_intel_sources"]["Mixpanel"] == 1
        mock_search.web_search.assert_awaited()
