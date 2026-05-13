# tests/test_mox_instantly.py
"""Tests for Mox Instantly AI integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_origin.core.mox import Mox
from devrel_origin.tools.instantly_client import CampaignAnalytics, InstantlyCampaign


@pytest.fixture
def mock_instantly():
    client = MagicMock()
    client.create_campaign = AsyncMock(
        return_value=InstantlyCampaign(
            id="camp_1",
            name="Q1 Outreach",
            status="draft",
            accounts=["sender@co.com"],
            sequences=[],
        )
    )
    client.list_campaigns = AsyncMock(
        return_value=[
            InstantlyCampaign(id="c1", name="A", status="active", accounts=[], sequences=[]),
            InstantlyCampaign(id="c2", name="B", status="active", accounts=[], sequences=[]),
        ]
    )
    client.get_campaign_analytics = AsyncMock(
        side_effect=[
            CampaignAnalytics(
                campaign_id="c1",
                campaign_name="A",
                total_leads=100,
                emails_sent=80,
                emails_opened=40,
                emails_replied=10,
                emails_bounced=5,
                open_rate=0.5,
                reply_rate=0.125,
                bounce_rate=0.0625,
            ),
            CampaignAnalytics(
                campaign_id="c2",
                campaign_name="B",
                total_leads=50,
                emails_sent=40,
                emails_opened=20,
                emails_replied=5,
                emails_bounced=2,
                open_rate=0.5,
                reply_rate=0.125,
                bounce_rate=0.05,
            ),
        ]
    )
    client.activate_campaign = AsyncMock(return_value={"status": "active"})
    client.close = AsyncMock()
    return client


@pytest.fixture
def mox_with_instantly(posthog_client, knowledge_base_path, mock_llm_client, mock_instantly):
    return Mox(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        instantly_client=mock_instantly,
    )


class TestMoxPushCampaign:
    """Test campaign creation in Instantly."""

    @pytest.mark.asyncio
    async def test_push_campaign_creates(self, mox_with_instantly, mock_instantly):
        result = await mox_with_instantly.push_campaign(
            campaign_name="Q1 Outreach",
            email_sequences=[
                {"subject": "Hello {{first_name}}", "body": "Intro email", "delay_days": 0},
                {"subject": "Following up", "body": "Just checking in", "delay_days": 3},
            ],
            accounts=["sender@co.com"],
        )
        assert result["campaign_id"] == "camp_1"
        mock_instantly.create_campaign.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_campaign_no_client(
        self, posthog_client, knowledge_base_path, mock_llm_client
    ):
        mox = Mox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        result = await mox.push_campaign("Test", [{"subject": "Hi", "body": "Hey"}])
        assert result["error"] == "No Instantly client configured"


class TestMoxPullStats:
    """Test analytics aggregation."""

    @pytest.mark.asyncio
    async def test_pull_stats_aggregates(self, mox_with_instantly):
        stats = await mox_with_instantly.pull_campaign_stats()
        assert stats["total_campaigns"] == 2
        assert stats["total_sent"] == 120  # 80 + 40
        assert stats["total_replied"] == 15  # 10 + 5
        assert stats["avg_reply_rate"] == 0.125  # both are 0.125

    @pytest.mark.asyncio
    async def test_pull_stats_specific_ids(self, mox_with_instantly, mock_instantly):
        mock_instantly.get_campaign_analytics = AsyncMock(
            return_value=CampaignAnalytics(
                campaign_id="c1",
                campaign_name="A",
                total_leads=100,
                emails_sent=80,
                emails_opened=40,
                emails_replied=10,
                emails_bounced=5,
                open_rate=0.5,
                reply_rate=0.125,
                bounce_rate=0.0625,
            )
        )
        stats = await mox_with_instantly.pull_campaign_stats(campaign_ids=["c1"])
        assert stats["total_campaigns"] == 1

    @pytest.mark.asyncio
    async def test_pull_stats_no_client(self, posthog_client, knowledge_base_path, mock_llm_client):
        mox = Mox(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        stats = await mox.pull_campaign_stats()
        assert stats["total_campaigns"] == 0


class TestMoxEmailCampaignExecute:
    """Test the email_campaign path via execute()."""

    @pytest.mark.asyncio
    async def test_execute_email_campaign_type(self, mox_with_instantly):
        result = await mox_with_instantly.execute("Create a cold email drip campaign")
        assert result["agent"] == "mox"
        assert result["content_type"] == "email_campaign"
