"""Tests for Echo social media listener agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.echo import (
    ENGAGEMENT_SIGNALS,
    RISK_SIGNALS,
    Echo,
    PlatformSummary,
    SocialListeningReport,
    SocialMention,
)
from tools.search_tools import SearchResult


@pytest.fixture
def mock_search_tools():
    """Fixture providing mocked search tools."""
    st = MagicMock()
    st.web_search = AsyncMock(return_value=[])
    st.close = AsyncMock()
    return st


@pytest.fixture
def echo(posthog_client, knowledge_base_path, mock_search_tools):
    return Echo(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        search_tools=mock_search_tools,
    )


@pytest.fixture
def echo_no_tools(posthog_client, knowledge_base_path):
    return Echo(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
    )


@pytest.fixture
def sample_search_results():
    """Search results that mention OpenClaw."""
    return [
        SearchResult(
            title="OpenClaw vs other AI assistants: Which should I use?",
            url="https://reddit.com/r/selfhosted/comments/abc123/devrel_ai_agents_vs_others",
            snippet="I'm comparing OpenClaw vs other AI assistants. OpenClaw seems great for self-hosted.",
            source="web",
        ),
        SearchResult(
            title="Anyone using OpenClaw for messaging?",
            url="https://reddit.com/r/devops/comments/def456/anyone_using_devrel_ai_agents",
            snippet="We're looking for an AI assistant. Anyone using OpenClaw? How is it?",
            source="web",
        ),
        SearchResult(
            title="Terrible experience with OpenClaw",
            url="https://reddit.com/r/startups/comments/ghi789/terrible_devrel_ai_agents",
            snippet="Worst AI assistant I've used. OpenClaw keeps crashing. Switching away.",
            source="web",
        ),
        SearchResult(
            title="Unrelated post about AI tools",
            url="https://reddit.com/r/artificial/comments/jkl012/unrelated",
            snippet="We use ChatGPT and it's working fine for our needs.",
            source="web",
        ),
    ]


class TestEchoExecute:
    """Test execute() entry point."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, echo):
        result = await echo.execute("Scan social media for PostHog mentions")
        assert result["agent"] == "echo"
        assert result["status"] == "scanned"
        assert "total_mentions" in result
        assert "platforms" in result
        assert "sentiment_overall" in result
        assert "engagement_opportunities" in result
        assert "reputation_risks" in result

    @pytest.mark.asyncio
    async def test_execute_with_no_search_tools(self, echo_no_tools):
        result = await echo_no_tools.execute("Scan social media")
        assert result["total_mentions"] == 0
        assert result["platforms"] == {}

    @pytest.mark.asyncio
    async def test_execute_with_search_results(self, echo, mock_search_tools, sample_search_results):
        mock_search_tools.web_search = AsyncMock(return_value=sample_search_results)
        result = await echo.execute("Scan social media")
        # Should find mentions (excluding the unrelated post)
        assert result["total_mentions"] > 0

    @pytest.mark.asyncio
    async def test_execute_brand_default(self, echo):
        result = await echo.execute("Scan mentions")
        assert result["brand"] == "OpenClaw"


class TestSentimentClassification:
    """Test _classify_sentiment() rule-based classifier."""

    def test_positive_sentiment(self, echo):
        assert echo._classify_sentiment("PostHog is amazing and I love it") == "positive"

    def test_negative_sentiment(self, echo):
        assert echo._classify_sentiment("terrible experience, broken features") == "negative"

    def test_neutral_sentiment(self, echo):
        assert echo._classify_sentiment("PostHog released a new version today") == "neutral"

    def test_mixed_defaults_to_count(self, echo):
        # More positive than negative signals
        text = "love this awesome tool despite one broken feature"
        assert echo._classify_sentiment(text) == "positive"


class TestParseSearchResult:
    """Test _parse_search_result() conversion."""

    def test_parses_reddit_result_with_mention(self, echo):
        result = SearchResult(
            title="PostHog analytics review",
            url="https://reddit.com/r/analytics/comments/abc/posthog_review",
            snippet="PostHog is a solid open-source analytics platform.",
            source="web",
        )
        mention = echo._parse_search_result(result, "reddit", ["posthog"])
        assert mention is not None
        assert mention.platform == "reddit"
        assert mention.subreddit == "analytics"
        assert mention.sentiment == "positive"  # "solid" is positive

    def test_skips_result_without_mention(self, echo):
        result = SearchResult(
            title="Best analytics tools 2026",
            url="https://reddit.com/r/analytics/comments/xyz",
            snippet="Mixpanel and Amplitude are great choices.",
            source="web",
        )
        mention = echo._parse_search_result(result, "reddit", ["posthog"])
        assert mention is None

    def test_extracts_subreddit_from_url(self, echo):
        result = SearchResult(
            title="PostHog question",
            url="https://reddit.com/r/selfhosted/comments/abc/posthog",
            snippet="Anyone self-hosting PostHog?",
            source="web",
        )
        mention = echo._parse_search_result(result, "reddit", ["posthog"])
        assert mention.subreddit == "selfhosted"

    def test_no_subreddit_for_non_reddit(self, echo):
        result = SearchResult(
            title="PostHog on HN",
            url="https://news.ycombinator.com/item?id=123",
            snippet="PostHog launched a new feature.",
            source="web",
        )
        mention = echo._parse_search_result(result, "hackernews", ["posthog"])
        assert mention.subreddit is None

    def test_detects_question_as_engagement_opportunity(self, echo):
        result = SearchResult(
            title="Looking for analytics tool",
            url="https://reddit.com/r/devops/comments/abc",
            snippet="Looking for an open-source analytics tool like PostHog. Anyone recommend it?",
            source="web",
        )
        mention = echo._parse_search_result(result, "reddit", ["posthog"])
        assert mention.is_question is True
        assert mention.requires_response is True


class TestEngagementOpportunities:
    """Test _find_engagement_opportunities()."""

    def test_finds_recommendation_opportunity(self, echo):
        mentions = [
            SocialMention(
                platform="reddit",
                title="Looking for analytics tool",
                url="https://reddit.com/r/analytics/abc",
                author="user1",
                content="Looking for a good analytics tool, anyone recommend PostHog?",
                sentiment="neutral",
                engagement=15,
                posted_at="2026-03-14",
            ),
        ]
        ops = echo._find_engagement_opportunities(mentions)
        assert len(ops) == 1
        assert "looking for" in ops[0]["reason"].lower() or "recommend" in ops[0]["reason"].lower()

    def test_no_opportunities_for_plain_mention(self, echo):
        mentions = [
            SocialMention(
                platform="hackernews",
                title="PostHog raised funding",
                url="https://news.ycombinator.com/item?id=123",
                author="user2",
                content="PostHog raised Series B funding.",
                sentiment="neutral",
                engagement=100,
                posted_at="2026-03-14",
            ),
        ]
        ops = echo._find_engagement_opportunities(mentions)
        assert len(ops) == 0

    def test_limits_to_ten(self, echo):
        mentions = [
            SocialMention(
                platform="reddit",
                title=f"Looking for tool {i}",
                url=f"https://reddit.com/r/test/{i}",
                author=f"user{i}",
                content=f"Looking for PostHog alternative to compare {i}",
                sentiment="neutral",
                engagement=i,
                posted_at="2026-03-14",
            )
            for i in range(15)
        ]
        ops = echo._find_engagement_opportunities(mentions)
        assert len(ops) <= 10


class TestReputationRisks:
    """Test _flag_reputation_risks()."""

    def test_flags_negative_post(self, echo):
        mentions = [
            SocialMention(
                platform="reddit",
                title="Terrible experience with PostHog",
                url="https://reddit.com/r/startups/abc",
                author="angry-user",
                content="Terrible experience. Switching away from PostHog. Avoid it.",
                sentiment="negative",
                engagement=50,
                posted_at="2026-03-14",
            ),
        ]
        risks = echo._flag_reputation_risks(mentions)
        assert len(risks) == 1
        assert risks[0]["severity"] == "high"  # multiple risk signals

    def test_no_risks_for_positive_post(self, echo):
        mentions = [
            SocialMention(
                platform="twitter",
                title="Love PostHog",
                url="https://twitter.com/user/status/123",
                author="fan",
                content="PostHog is amazing for analytics!",
                sentiment="positive",
                engagement=25,
                posted_at="2026-03-14",
            ),
        ]
        risks = echo._flag_reputation_risks(mentions)
        assert len(risks) == 0


class TestPlatformSummaries:
    """Test _build_platform_summaries()."""

    def test_groups_by_platform(self, echo):
        mentions = [
            SocialMention(
                platform="reddit", title="Post 1", url="url1", author="a",
                content="PostHog", sentiment="positive", engagement=10, posted_at="2026-03-14",
            ),
            SocialMention(
                platform="reddit", title="Post 2", url="url2", author="b",
                content="PostHog", sentiment="negative", engagement=5, posted_at="2026-03-14",
            ),
            SocialMention(
                platform="hackernews", title="Post 3", url="url3", author="c",
                content="PostHog", sentiment="neutral", engagement=50, posted_at="2026-03-14",
            ),
        ]
        summaries = echo._build_platform_summaries(mentions)
        assert len(summaries) == 2
        platforms = {s.platform for s in summaries}
        assert platforms == {"reddit", "hackernews"}

    def test_sentiment_breakdown_per_platform(self, echo):
        mentions = [
            SocialMention(
                platform="reddit", title="Post 1", url="u1", author="a",
                content="PostHog", sentiment="positive", engagement=10, posted_at="2026-03-14",
            ),
            SocialMention(
                platform="reddit", title="Post 2", url="u2", author="b",
                content="PostHog", sentiment="positive", engagement=5, posted_at="2026-03-14",
            ),
            SocialMention(
                platform="reddit", title="Post 3", url="u3", author="c",
                content="PostHog", sentiment="negative", engagement=1, posted_at="2026-03-14",
            ),
        ]
        summaries = echo._build_platform_summaries(mentions)
        reddit = summaries[0]
        assert reddit.sentiment_breakdown["positive"] == 2
        assert reddit.sentiment_breakdown["negative"] == 1


class TestAggregateSentiment:
    """Test _aggregate_sentiment()."""

    def test_counts_all_sentiments(self, echo):
        mentions = [
            SocialMention(
                platform="reddit", title="t", url="u", author="a",
                content="c", sentiment="positive", engagement=0, posted_at="d",
            ),
            SocialMention(
                platform="reddit", title="t", url="u", author="a",
                content="c", sentiment="positive", engagement=0, posted_at="d",
            ),
            SocialMention(
                platform="reddit", title="t", url="u", author="a",
                content="c", sentiment="negative", engagement=0, posted_at="d",
            ),
        ]
        result = echo._aggregate_sentiment(mentions)
        assert result["positive"] == 2
        assert result["negative"] == 1
        assert result["neutral"] == 0

    def test_empty_mentions(self, echo):
        result = echo._aggregate_sentiment([])
        assert result == {"positive": 0, "neutral": 0, "negative": 0}


class TestExtractTopics:
    """Test _extract_topics()."""

    def test_extracts_matching_topics(self, echo):
        mentions = [
            SocialMention(
                platform="reddit", title="OpenClaw voice assistant review", url="u",
                author="a", content="Voice and privacy features are great",
                sentiment="positive", engagement=10, posted_at="d",
            ),
            SocialMention(
                platform="reddit", title="OpenClaw voice setup on WhatsApp", url="u2",
                author="b", content="How to set up voice on WhatsApp with OpenClaw",
                sentiment="neutral", engagement=5, posted_at="d",
            ),
        ]
        topics = echo._extract_topics(mentions)
        assert "voice" in topics
        assert len(topics) <= 5


class TestScanWeekly:
    """Test scan_weekly() integration."""

    @pytest.mark.asyncio
    async def test_scan_weekly_returns_report(self, echo):
        report = await echo.scan_weekly(brand="PostHog")
        assert isinstance(report, SocialListeningReport)
        assert report.period == "weekly"
        assert report.brand == "PostHog"

    @pytest.mark.asyncio
    async def test_scan_weekly_with_results(self, echo, mock_search_tools, sample_search_results):
        mock_search_tools.web_search = AsyncMock(return_value=sample_search_results)
        report = await echo.scan_weekly(brand="OpenClaw")
        assert report.total_mentions > 0


class TestSuggestEngagementAction:
    """Test _suggest_engagement_action() static method."""

    def test_recommendation_action(self):
        action = Echo._suggest_engagement_action(["looking for", "recommend"])
        assert "non-salesy" in action.lower() or "need" in action.lower()

    def test_comparison_action(self):
        action = Echo._suggest_engagement_action(["vs", "comparison"])
        assert "comparison" in action.lower()

    def test_howto_action(self):
        action = Echo._suggest_engagement_action(["how to"])
        assert "documentation" in action.lower() or "tutorial" in action.lower()

    def test_migration_action(self):
        action = Echo._suggest_engagement_action(["migrating from"])
        assert "migration" in action.lower() or "getting-started" in action.lower()

    def test_generic_action(self):
        action = Echo._suggest_engagement_action(["unknown signal"])
        assert len(action) > 0
