"""Tests for Mox campaign marketing agent."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devrel_origin.core.mox import (
    PIPELINE_CONTENT_TYPE_MAP,
    BlogPost,
    CampaignBrief,
    LandingPageCopy,
    Mox,
    PressRelease,
    SocialBatch,
)


@pytest.fixture
def mock_search_tools():
    st = MagicMock()
    st.web_search = AsyncMock(return_value=[])
    st.close = AsyncMock()
    return st


@pytest.fixture
def mox(posthog_client, knowledge_base_path, mock_llm_client, mock_search_tools):
    return Mox(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        search_tools=mock_search_tools,
    )


@pytest.fixture
def mox_no_llm(posthog_client, knowledge_base_path):
    return Mox(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
    )


class TestMoxDataclasses:
    """Test dataclass construction."""

    def test_blog_post(self):
        post = BlogPost(
            title="Best Open-Source AI Assistants in 2026",
            body="Content here...",
            meta_description="Compare top AI assistants.",
            target_keywords=["ai assistant", "open source"],
            cta="Try OpenClaw free",
            word_count=1200,
        )
        assert post.word_count == 1200

    def test_landing_page_copy(self):
        lp = LandingPageCopy(
            hero_headline="Your AI, Your Rules",
            hero_subhead="Run locally, connect everywhere.",
            features=[{"title": "Multi-channel", "description": "15+ integrations"}],
            social_proof=["500+ stars on GitHub"],
            cta_primary="Get Started Free",
            cta_secondary="See Demo",
            seo_title="OpenClaw - Open Source AI Assistant",
            seo_description="Run AI on your own devices.",
        )
        assert lp.hero_headline == "Your AI, Your Rules"

    def test_social_batch(self):
        batch = SocialBatch(
            platform="twitter",
            campaign_name="Launch week",
            posts=[{"text": "Announcing...", "hook": "hook", "cta": "Try it"}],
            hashtags=["#OpenClaw", "#AIAssistant"],
        )
        assert batch.platform == "twitter"

    def test_campaign_brief(self):
        brief = CampaignBrief(
            name="Voice Launch",
            goal="Drive awareness",
            positioning="First open-source voice-enabled assistant",
            messages=["primary msg", "secondary msg"],
            channels=["twitter", "blog"],
            timeline=[{"day": "1", "action": "Blog post", "owner": "Mox"}],
            draft_assets=["blog post", "social batch"],
        )
        assert brief.name == "Voice Launch"

    def test_press_release(self):
        pr = PressRelease(
            headline="OpenClaw 1.0 Released",
            subhead="Open-source AI assistant goes GA",
            body="Content...",
            quotes=[{"speaker": "CEO", "title": "Founder", "quote": "We're excited"}],
            boilerplate="OpenClaw is...",
            contact="press@example.com",
        )
        assert pr.headline == "OpenClaw 1.0 Released"


class TestMoxTaskParsing:
    """Test _parse_content_type() keyword matching."""

    def test_blog_post(self, mox):
        assert mox._parse_content_type("Write an SEO blog post about AI assistants") == "blog"

    def test_landing_page(self, mox):
        assert (
            mox._parse_content_type("Write landing page copy for WhatsApp integration")
            == "landing_page"
        )

    def test_social_batch(self, mox):
        assert mox._parse_content_type("Generate social media posts for Twitter") == "social"

    def test_campaign_brief(self, mox):
        assert mox._parse_content_type("Create a product launch campaign") == "campaign"

    def test_press_release(self, mox):
        assert mox._parse_content_type("Write a press release for 1.0") == "press_release"

    def test_announcement_maps_to_press_release(self, mox):
        assert (
            mox._parse_content_type("Write an announcement for the new feature") == "press_release"
        )

    def test_case_study(self, mox):
        assert mox._parse_content_type("Create a case study framework for DevOps") == "case_study"

    def test_linkedin_maps_to_social(self, mox):
        assert mox._parse_content_type("Write LinkedIn posts for the team") == "social"

    def test_default_fallback(self, mox):
        assert mox._parse_content_type("Create some marketing content") == "blog"


class TestMoxUpstreamContext:
    """Test _extract_upstream_context()."""

    def test_extracts_rex_competitive(self, mox):
        context = {
            "rex_competitive": {
                "profiles": [{"name": "Botpress", "strengths": ["visual builder"]}],
            },
        }
        extracted = mox._extract_upstream_context(context)
        assert len(extracted["competitors"]) == 1

    def test_extracts_iris_pain_points(self, mox):
        context = {
            "iris_themes": {
                "themes": [{"title": "Setup complexity", "severity": 7.0}],
            },
        }
        extracted = mox._extract_upstream_context(context)
        assert len(extracted["pain_points"]) == 1

    def test_extracts_kai_content(self, mox):
        context = {
            "kai_content": {
                "content": "Tutorial: How to set up voice channels",
                "grounding_sources": ["features/voice.md"],
            },
        }
        extracted = mox._extract_upstream_context(context)
        assert "Tutorial" in extracted["existing_content"]

    def test_handles_empty_context(self, mox):
        extracted = mox._extract_upstream_context(None)
        assert extracted["competitors"] == []
        assert extracted["pain_points"] == []
        assert extracted["existing_content"] == ""


class TestMoxExecute:
    """Test execute() entry point."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, mox):
        result = await mox.execute("Write an SEO blog post about AI assistants")
        assert result["agent"] == "mox"
        assert result["content_type"] == "blog"
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm(self, mox_no_llm):
        result = await mox_no_llm.execute("Write landing page copy")
        assert result["agent"] == "mox"
        assert result["content_type"] == "landing_page"
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_with_upstream_context(self, mox):
        context = {
            "rex_competitive": {
                "profiles": [{"name": "Botpress"}],
            },
            "iris_themes": {
                "themes": [{"title": "Setup pain", "severity": 8.0}],
            },
            "kai_content": {
                "content": "Tutorial content here",
            },
        }
        result = await mox.execute("Generate social media posts", context=context)
        assert result["agent"] == "mox"
        assert result["content_type"] == "social"


class TestMoxPipelineRouting:
    """Test PIPELINE_CONTENT_TYPE_MAP coverage and the email_campaign fallback."""

    def test_pipeline_map_covers_all_routed_types(self, mox):
        # Every CONTENT_KEYWORDS key must have an explicit pipeline mapping
        # so we never silently default to blog_post.
        for content_type in mox.CONTENT_KEYWORDS:
            assert content_type in PIPELINE_CONTENT_TYPE_MAP, (
                f"content_type {content_type!r} missing from PIPELINE_CONTENT_TYPE_MAP"
            )

    @pytest.mark.asyncio
    async def test_email_campaign_fallback_uses_clean_prose_prompt(
        self, posthog_client, knowledge_base_path, mock_llm_client
    ):
        # When push_campaign fails, the editorial-pipeline fallback must
        # receive the CLEAN prose prompt, NOT the JSON-formatted email_prompt.
        instantly = MagicMock()
        mox = Mox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            instantly_client=instantly,
        )
        # Make the email_campaign-specific generate fail so we fall through
        mock_llm_client.generate = AsyncMock(side_effect=RuntimeError("instantly down"))
        captured: dict = {}

        async def fake_pipeline(*, user_prompt, content_type, **_):
            captured["user_prompt"] = user_prompt
            captured["content_type"] = content_type
            return ("body", [], [])

        with patch("devrel_origin.core.mox.generate_with_pipeline", new=fake_pipeline):
            result = await mox.execute("Build a cold email drip campaign")

        assert result["content_type"] == "email_campaign"
        # The JSON output contract must NOT leak into the editorial fallback
        assert "Output Format" not in captured["user_prompt"]
        assert '"sequences"' not in captured["user_prompt"]
        # email_campaign should now route through the pipeline (mapped to blog_post)
        assert captured["content_type"] == "blog_post"
