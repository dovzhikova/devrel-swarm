# tests/test_instantly_client.py
"""Tests for Instantly AI API client."""

import httpx
import pytest
import respx

from devrel_swarm.tools.instantly_client import (
    CampaignAnalytics,
    InstantlyAPIError,
    InstantlyCampaign,
    InstantlyClient,
    InstantlyEmail,
    InstantlyLead,
)


class TestInstantlyDTOs:
    """Test dataclass creation and serialization."""

    def test_lead_defaults(self):
        lead = InstantlyLead(email="test@example.com")
        assert lead.email == "test@example.com"
        assert lead.first_name == ""
        assert lead.custom_variables == {}

    def test_lead_to_dict(self):
        lead = InstantlyLead(
            email="test@example.com",
            first_name="Ada",
            last_name="Lovelace",
            company_name="Babbage Inc",
            custom_variables={"role": "Engineer"},
        )
        d = lead.to_api_dict()
        assert d["email"] == "test@example.com"
        assert d["first_name"] == "Ada"
        assert d["variables"] == {"role": "Engineer"}

    def test_campaign_creation(self):
        c = InstantlyCampaign(
            id="camp_123", name="Q1 Outreach", status="draft",
            accounts=["sender@co.com"], sequences=[],
        )
        assert c.status == "draft"

    def test_email_with_thread(self):
        e = InstantlyEmail(
            id="em_1", campaign_id="camp_1", lead_email="lead@co.com",
            subject="Hi", body="Hello", is_reply=True, timestamp="2026-03-17",
            thread_id="thread_abc",
        )
        assert e.thread_id == "thread_abc"

    def test_email_without_thread(self):
        e = InstantlyEmail(
            id="em_2", campaign_id="camp_1", lead_email="lead@co.com",
            subject="Hi", body="Hello", is_reply=False, timestamp="2026-03-17",
        )
        assert e.thread_id is None

    def test_analytics_rates(self):
        a = CampaignAnalytics(
            campaign_id="c1", campaign_name="Test", total_leads=100,
            emails_sent=80, emails_opened=40, emails_replied=10,
            emails_bounced=5, open_rate=0.5, reply_rate=0.125, bounce_rate=0.0625,
        )
        assert a.open_rate == 0.5

    def test_instantly_api_error(self):
        err = InstantlyAPIError("Bad request", status_code=400, response_body={"error": "invalid"})
        assert err.status_code == 400
        assert "Bad request" in str(err)


@pytest.fixture
def instantly_client():
    return InstantlyClient(api_key="test-key", base_url="https://api.instantly.ai")


class TestInstantlyClientRequest:
    """Test the core _request method."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_get(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/campaigns").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        result = await instantly_client._request("GET", "/api/v2/campaigns")
        assert result == {"items": []}

    @pytest.mark.asyncio
    @respx.mock
    async def test_4xx_raises_api_error(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/campaigns/bad").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        with pytest.raises(InstantlyAPIError) as exc_info:
            await instantly_client._request("GET", "/api/v2/campaigns/bad")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @respx.mock
    async def test_bearer_auth_header(self, instantly_client):
        route = respx.get("https://api.instantly.ai/api/v2/campaigns").mock(
            return_value=httpx.Response(200, json={})
        )
        await instantly_client._request("GET", "/api/v2/campaigns")
        assert route.calls[0].request.headers["Authorization"] == "Bearer test-key"


class TestCampaignMethods:
    """Test campaign CRUD and activation."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_campaign(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/campaigns").mock(
            return_value=httpx.Response(200, json={
                "id": "camp_1", "name": "Test", "status": "draft",
                "accounts": [], "sequences": [],
            })
        )
        campaign = await instantly_client.create_campaign(
            name="Test", sequences=[], accounts=["sender@co.com"],
        )
        assert campaign.id == "camp_1"
        assert campaign.status == "draft"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_campaign(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/campaigns/camp_1").mock(
            return_value=httpx.Response(200, json={
                "id": "camp_1", "name": "Test", "status": "active",
                "accounts": [], "sequences": [],
            })
        )
        campaign = await instantly_client.get_campaign("camp_1")
        assert campaign.name == "Test"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_campaigns(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/campaigns").mock(
            return_value=httpx.Response(200, json={
                "items": [
                    {"id": "c1", "name": "A", "status": "active", "accounts": [], "sequences": []},
                    {"id": "c2", "name": "B", "status": "draft", "accounts": [], "sequences": []},
                ],
            })
        )
        campaigns = await instantly_client.list_campaigns()
        assert len(campaigns) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_activate_campaign(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/campaigns/camp_1/activate").mock(
            return_value=httpx.Response(200, json={"status": "active"})
        )
        result = await instantly_client.activate_campaign("camp_1")
        assert result["status"] == "active"

    @pytest.mark.asyncio
    @respx.mock
    async def test_stop_campaign(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/campaigns/camp_1/stop").mock(
            return_value=httpx.Response(200, json={"status": "paused"})
        )
        result = await instantly_client.stop_campaign("camp_1")
        assert result["status"] == "paused"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_campaign_analytics(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/campaigns/camp_1/analytics/overview").mock(
            return_value=httpx.Response(200, json={
                "campaign_id": "camp_1", "campaign_name": "Test",
                "total_leads": 100, "emails_sent": 80, "emails_opened": 40,
                "emails_replied": 10, "emails_bounced": 5,
                "open_rate": 0.5, "reply_rate": 0.125, "bounce_rate": 0.0625,
            })
        )
        analytics = await instantly_client.get_campaign_analytics("camp_1")
        assert analytics.reply_rate == 0.125
        assert analytics.total_leads == 100


class TestLeadMethods:
    """Test lead CRUD and bulk operations."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_lead(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/leads").mock(
            return_value=httpx.Response(200, json={"id": "lead_1", "email": "test@co.com"})
        )
        result = await instantly_client.create_lead(
            email="test@co.com", campaign_id="camp_1",
            first_name="Ada", last_name="Lovelace",
        )
        assert result["email"] == "test@co.com"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_leads_bulk(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/leads/bulk-add").mock(
            return_value=httpx.Response(200, json={"added": 3, "skipped": 0})
        )
        leads = [
            InstantlyLead(email=f"user{i}@co.com", first_name=f"User{i}")
            for i in range(3)
        ]
        result = await instantly_client.add_leads_bulk("camp_1", leads)
        assert result["added"] == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_leads(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/leads/list").mock(
            return_value=httpx.Response(200, json={
                "items": [{"id": "l1", "email": "a@b.com"}],
            })
        )
        result = await instantly_client.list_leads("camp_1")
        assert len(result) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_lead_interest(self, instantly_client):
        respx.patch("https://api.instantly.ai/api/v2/leads/lead_1/interest-status").mock(
            return_value=httpx.Response(200, json={"status": "interested"})
        )
        result = await instantly_client.update_lead_interest("lead_1", "interested")
        assert result["status"] == "interested"


class TestEmailMethods:
    """Test email listing and replying."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_emails(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/emails").mock(
            return_value=httpx.Response(200, json={
                "items": [
                    {"id": "e1", "campaign_id": "c1", "lead_email": "a@b.com",
                     "subject": "Hi", "body": "Hello", "is_reply": True,
                     "timestamp": "2026-03-17", "thread_id": "t1"},
                ],
            })
        )
        emails = await instantly_client.list_emails(campaign_id="c1", is_reply=True)
        assert len(emails) == 1
        assert emails[0].is_reply is True
        assert emails[0].thread_id == "t1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_reply_to_email(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/emails/reply").mock(
            return_value=httpx.Response(200, json={"id": "reply_1", "status": "sent"})
        )
        result = await instantly_client.reply_to_email(
            email_id="e1", campaign_id="c1", body="Thanks for your interest!",
            thread_id="t1",
        )
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_emails_no_filter(self, instantly_client):
        respx.get("https://api.instantly.ai/api/v2/emails").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        emails = await instantly_client.list_emails()
        assert emails == []


class TestLeadListMethods:
    """Test lead list operations."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_lead_list(self, instantly_client):
        respx.post("https://api.instantly.ai/api/v2/lead-lists").mock(
            return_value=httpx.Response(200, json={"id": "ll_1", "name": "Q1 Leads"})
        )
        result = await instantly_client.create_lead_list("Q1 Leads")
        assert result["name"] == "Q1 Leads"
