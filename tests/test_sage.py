"""Tests for Sage community manager module."""

import pytest

from devrel_swarm.core.sage import IssuePriority, Sage, SentimentScore


@pytest.fixture
def sage(posthog_client, knowledge_base_path):
    return Sage(api_client=posthog_client, knowledge_base_path=knowledge_base_path)


class TestSageSentimentAnalysis:
    """Test _analyze_sentiment() with various inputs."""

    def test_positive_sentiment(self, sage):
        result = sage._analyze_sentiment("This library is amazing! Love the docs.")
        assert result == SentimentScore.POSITIVE

    def test_negative_churning_sentiment(self, sage):
        result = sage._analyze_sentiment("I'm switching to a different tool. Give up.")
        assert result == SentimentScore.CHURNING

    def test_frustrated_sentiment(self, sage):
        result = sage._analyze_sentiment("This is terrible and broken!!!")
        assert result == SentimentScore.FRUSTRATED

    def test_neutral_sentiment(self, sage):
        result = sage._analyze_sentiment("The feature exists but has limitations.")
        assert result == SentimentScore.NEUTRAL


class TestSageIssueCategorization:
    """Test _categorize_issue() keyword matching."""

    def test_categorize_bug(self, sage):
        category = sage._categorize_issue("Error in production", "The app crashes on load")
        assert category == "bug"

    def test_categorize_feature_request(self, sage):
        category = sage._categorize_issue("Feature request", "Would be nice to have dark mode")
        assert category == "feature_request"

    def test_categorize_documentation(self, sage):
        category = sage._categorize_issue("Docs unclear", "The documentation needs examples")
        assert category == "docs"

    def test_categorize_performance(self, sage):
        category = sage._categorize_issue("Slow queries", "Latency is very high")
        assert category == "performance"

    def test_categorize_question(self, sage):
        category = sage._categorize_issue("How to set up?", "Help me configure this")
        assert category == "question"


class TestSageProductAreaDetection:
    """Test _detect_product_area() mapping."""

    def test_detect_channels_area(self, sage):
        area = sage._detect_product_area("WhatsApp integration", "Telegram channel not working")
        assert area == "channels"

    def test_detect_gateway_area(self, sage):
        area = sage._detect_product_area("Local gateway", "Proxy routing broken")
        assert area == "gateway"

    def test_detect_skills_area(self, sage):
        area = sage._detect_product_area("Custom skill issue", "Plugin extension broken")
        assert area == "skills"

    def test_detect_voice_area(self, sage):
        area = sage._detect_product_area("Voice input", "Speech recognition not working")
        assert area == "voice"

    def test_default_area(self, sage):
        area = sage._detect_product_area("Random issue", "Something unrelated")
        assert area == "agents"  # default fallback


class TestSagePriorityScoring:
    """Test _score_priority() combinations."""

    def test_critical_priority(self, sage):
        priority = sage._score_priority(
            "Data loss", "Security vulnerability found", SentimentScore.FRUSTRATED
        )
        assert priority == IssuePriority.CRITICAL

    def test_high_priority_churning(self, sage):
        priority = sage._score_priority("Minor issue", "Something small", SentimentScore.CHURNING)
        assert priority == IssuePriority.HIGH

    def test_high_priority_broken(self, sage):
        priority = sage._score_priority(
            "Cannot login", "Unable to access dashboard", SentimentScore.NEUTRAL
        )
        assert priority == IssuePriority.HIGH

    def test_medium_priority(self, sage):
        priority = sage._score_priority("UI glitch", "Button misaligned", SentimentScore.FRUSTRATED)
        assert priority == IssuePriority.MEDIUM

    def test_low_priority(self, sage):
        priority = sage._score_priority(
            "Nice to have", "Would like a color option", SentimentScore.NEUTRAL
        )
        assert priority == IssuePriority.LOW


class TestSageTriageIssue:
    """Test triage_issue() end-to-end."""

    @pytest.mark.asyncio
    async def test_triage_produces_result(self, sage):
        result = await sage.triage_issue(
            issue_number=42,
            title="Bug: crash on startup",
            body="The app crashes immediately",
            author="testuser",
        )
        assert result.issue_number == 42
        assert result.author == "testuser"
        assert result.category == "bug"
        assert result.suggested_response is not None


class TestSageExecuteWired:
    """Test that execute() calls GitHubTools and triage_issue()."""

    @pytest.fixture
    def wired_sage(self, posthog_client, knowledge_base_path, mock_github_tools):
        return Sage(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            github_tools=mock_github_tools,
        )

    @pytest.mark.asyncio
    async def test_execute_triages_github_issues(self, wired_sage, mock_github_tools):
        result = await wired_sage.execute("Triage GitHub issues from the past 7 days")
        assert result["status"] == "triaged"
        assert len(result["issues"]) == 3
        mock_github_tools.fetch_recent_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_detects_churn_risk(self, wired_sage):
        result = await wired_sage.execute("Triage issues")
        churn_risks = result["churn_risks"]
        assert "frustrated-dev" in churn_risks

    @pytest.mark.asyncio
    async def test_execute_populates_breakdowns(self, wired_sage):
        result = await wired_sage.execute("Triage issues")
        assert result["sentiment_breakdown"]["churning"] >= 1
        assert result["category_breakdown"]["bug"] >= 1

    @pytest.mark.asyncio
    async def test_execute_without_github_tools_returns_empty(
        self, posthog_client, knowledge_base_path
    ):
        sage = Sage(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        result = await sage.execute("Triage issues")
        assert result["issues"] == []
        assert result["status"] == "triaged"


class TestSageChampionSignal:
    """champion_signal must reflect actual engagement, not always be False."""

    @pytest.mark.asyncio
    async def test_champion_signal_set_when_comments_high(self, sage):
        """High comment count on an issue is a champion signal."""
        triaged = await sage.triage_issue(
            issue_number=1,
            title="Bug",
            body="x",
            author="ada",
            comments_count=5,
            reactions_total=0,
        )
        assert triaged.champion_signal is True

    @pytest.mark.asyncio
    async def test_champion_signal_off_when_low_engagement(self, sage):
        triaged = await sage.triage_issue(
            issue_number=2,
            title="Bug",
            body="no PR mentioned",
            author="bob",
            comments_count=0,
            reactions_total=0,
        )
        assert triaged.champion_signal is False


class TestSageChurningResponse:
    """A CHURNING user gets an empathetic response, not the generic triage line."""

    @pytest.mark.asyncio
    async def test_churning_sentiment_gets_empathetic_response(self, sage):
        triaged = await sage.triage_issue(
            issue_number=3,
            title="i'm done with this",
            body="been broken for the third time, switching to a different tool",
            author="charlie",
        )
        assert triaged.sentiment == SentimentScore.CHURNING
        response = triaged.suggested_response.lower()
        assert "frustrating" in response
        assert "queue" not in response
