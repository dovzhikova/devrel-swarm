# Instantly AI Integration — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Instantly AI cold email platform so Pax and Mox can create campaigns, upload leads, pull analytics, and triage replies with human-in-the-loop approval.

**Architecture:** Thin async API client (`tools/instantly_client.py`) following `GitHubTools` pattern. Pax gets lead upload + reply triage. Mox gets campaign push + analytics pull. Atlas adds Stage 7 (Instantly sync) and `--review-replies` CLI.

**Tech Stack:** httpx, tenacity, respx (tests), dataclasses for DTOs

**Spec:** `docs/superpowers/specs/2026-03-17-instantly-ai-integration-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `tools/instantly_client.py` | Async Instantly API v2 client — DTOs, 13 methods, error handling |
| Modify | `agents/pax.py` | Add `instantly_client` param, `upload_leads()`, `draft_followups()`, new asset types |
| Modify | `agents/mox.py` | Add `instantly_client` param, `push_campaign()`, `pull_campaign_stats()`, new content type |
| Modify | `agents/atlas.py` | SharedContext fields, `to_dict()`, `load()`, Stage 7, `--review-replies`, wiring |
| Modify | `agents/types.py` | Add `InstantlyAnalyticsResult`, `InstantlyRepliesResult` TypedDicts |
| Modify | `config/env.example` | Add `INSTANTLY_API_KEY` |
| Modify | `config/agent_config.yaml` | Add `instantly:` section under `api_clients:` |
| Create | `tests/test_instantly_client.py` | Unit tests for all client methods |
| Create | `tests/test_pax_instantly.py` | Tests for lead upload + reply triage + follow-up drafting |
| Create | `tests/test_mox_instantly.py` | Tests for campaign push + analytics pull |
| Create | `tests/test_atlas_replies.py` | Tests for Stage 7 + SharedContext.load() |

---

## Chunk 1: Instantly API Client

### Task 1: DTOs and error class

**Files:**
- Create: `tools/instantly_client.py`
- Create: `tests/test_instantly_client.py`

- [ ] **Step 1: Write tests for DTOs**

```python
# tests/test_instantly_client.py
"""Tests for Instantly AI API client."""

from dataclasses import asdict

import pytest

from tools.instantly_client import (
    CampaignAnalytics,
    InstantlyAPIError,
    InstantlyCampaign,
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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `python3 -m pytest tests/test_instantly_client.py::TestInstantlyDTOs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.instantly_client'`

- [ ] **Step 3: Implement DTOs and error class**

```python
# tools/instantly_client.py
"""
Instantly AI API v2 async client.

Provides typed async access to Instantly's REST API for:
- Campaign creation, activation, and analytics
- Lead management (single and bulk)
- Email listing and reply sending
- Lead list management

Authentication: Bearer token via API key.
Rate limits: Emails endpoint 20 req/min; others higher.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InstantlyAPIError(Exception):
    """Non-retryable error from the Instantly API (4xx)."""

    def __init__(self, message: str, status_code: int = 0, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------


@dataclass
class InstantlyLead:
    """A lead to be added to an Instantly campaign."""

    email: str
    first_name: str = ""
    last_name: str = ""
    company_name: str = ""
    title: str = ""
    custom_variables: dict[str, str] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to Instantly API payload format."""
        d: dict[str, Any] = {"email": self.email}
        if self.first_name:
            d["first_name"] = self.first_name
        if self.last_name:
            d["last_name"] = self.last_name
        if self.company_name:
            d["company_name"] = self.company_name
        if self.title:
            d["title"] = self.title
        if self.custom_variables:
            d["variables"] = self.custom_variables
        return d


@dataclass
class InstantlyCampaign:
    """Represents an Instantly campaign."""

    id: str
    name: str
    status: str  # "draft", "active", "paused", "completed"
    accounts: list[str] = field(default_factory=list)
    sequences: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InstantlyEmail:
    """An email message from Instantly."""

    id: str
    campaign_id: str
    lead_email: str
    subject: str
    body: str
    is_reply: bool
    timestamp: str
    thread_id: str | None = None


@dataclass
class CampaignAnalytics:
    """Aggregated analytics for a campaign."""

    campaign_id: str
    campaign_name: str
    total_leads: int
    emails_sent: int
    emails_opened: int
    emails_replied: int
    emails_bounced: int
    open_rate: float
    reply_rate: float
    bounce_rate: float
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_instantly_client.py::TestInstantlyDTOs -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tools/instantly_client.py tests/test_instantly_client.py
git commit -m "feat(instantly): add DTOs and error class for Instantly API client"
```

---

### Task 2: Core client — `_request()`, `close()`, campaign methods

**Files:**
- Modify: `tools/instantly_client.py`
- Modify: `tests/test_instantly_client.py`

- [ ] **Step 1: Write tests for `_request`, campaign CRUD, and activation**

Add to `tests/test_instantly_client.py`:

```python
import httpx
import respx

from tools.instantly_client import InstantlyClient


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
```

- [ ] **Step 2: Run tests — expect ImportError or AttributeError**

Run: `python3 -m pytest tests/test_instantly_client.py::TestInstantlyClientRequest tests/test_instantly_client.py::TestCampaignMethods -v`
Expected: FAIL

- [ ] **Step 3: Implement client core + campaign methods**

Add to `tools/instantly_client.py`:

```python
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class InstantlyClient:
    """Async client for Instantly AI API v2."""

    def __init__(self, api_key: str, base_url: str = "https://api.instantly.ai"):
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _request(
        self,
        method: str,
        path: str,
        json: dict | list | None = None,
        params: dict | None = None,
    ) -> dict:
        """Send an HTTP request with retry on 5xx and 429."""
        response = await self._client.request(method, path, json=json, params=params)

        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()  # triggers tenacity retry

        if 400 <= response.status_code < 500:
            body = response.json() if response.content else {}
            raise InstantlyAPIError(
                f"Instantly API error {response.status_code}: {body}",
                status_code=response.status_code,
                response_body=body,
            )

        logger.info(
            "instantly_api_call",
            extra={"method": method, "path": path, "status": response.status_code},
        )
        return response.json() if response.content else {}

    # -- Campaigns --------------------------------------------------------

    async def create_campaign(
        self,
        name: str,
        sequences: list[dict],
        accounts: list[str] | None = None,
    ) -> InstantlyCampaign:
        """Create a new campaign."""
        payload: dict[str, Any] = {"name": name, "sequences": sequences}
        if accounts:
            payload["accounts"] = accounts
        data = await self._request("POST", "/api/v2/campaigns", json=payload)
        return InstantlyCampaign(
            id=data["id"], name=data["name"], status=data.get("status", "draft"),
            accounts=data.get("accounts", []), sequences=data.get("sequences", []),
        )

    async def get_campaign(self, campaign_id: str) -> InstantlyCampaign:
        """Get campaign details by ID."""
        data = await self._request("GET", f"/api/v2/campaigns/{campaign_id}")
        return InstantlyCampaign(
            id=data["id"], name=data["name"], status=data.get("status", ""),
            accounts=data.get("accounts", []), sequences=data.get("sequences", []),
        )

    async def list_campaigns(
        self, limit: int = 100, skip: int = 0,
    ) -> list[InstantlyCampaign]:
        """List campaigns with pagination."""
        data = await self._request(
            "GET", "/api/v2/campaigns", params={"limit": limit, "skip": skip},
        )
        return [
            InstantlyCampaign(
                id=c["id"], name=c["name"], status=c.get("status", ""),
                accounts=c.get("accounts", []), sequences=c.get("sequences", []),
            )
            for c in data.get("items", data if isinstance(data, list) else [])
        ]

    async def activate_campaign(self, campaign_id: str) -> dict:
        """Activate/resume a campaign."""
        return await self._request("POST", f"/api/v2/campaigns/{campaign_id}/activate")

    async def stop_campaign(self, campaign_id: str) -> dict:
        """Stop/pause a campaign."""
        return await self._request("POST", f"/api/v2/campaigns/{campaign_id}/stop")

    async def get_campaign_analytics(self, campaign_id: str) -> CampaignAnalytics:
        """Get analytics overview for a campaign."""
        data = await self._request(
            "GET", f"/api/v2/campaigns/{campaign_id}/analytics/overview",
        )
        return CampaignAnalytics(
            campaign_id=data.get("campaign_id", campaign_id),
            campaign_name=data.get("campaign_name", ""),
            total_leads=data.get("total_leads", 0),
            emails_sent=data.get("emails_sent", 0),
            emails_opened=data.get("emails_opened", 0),
            emails_replied=data.get("emails_replied", 0),
            emails_bounced=data.get("emails_bounced", 0),
            open_rate=data.get("open_rate", 0.0),
            reply_rate=data.get("reply_rate", 0.0),
            bounce_rate=data.get("bounce_rate", 0.0),
        )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_instantly_client.py::TestInstantlyClientRequest tests/test_instantly_client.py::TestCampaignMethods -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tools/instantly_client.py tests/test_instantly_client.py
git commit -m "feat(instantly): add core client with campaign methods"
```

---

### Task 3: Lead and email methods

**Files:**
- Modify: `tools/instantly_client.py`
- Modify: `tests/test_instantly_client.py`

- [ ] **Step 1: Write tests for lead and email methods**

Add to `tests/test_instantly_client.py`:

```python
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
```

- [ ] **Step 2: Run tests — expect AttributeError**

Run: `python3 -m pytest tests/test_instantly_client.py::TestLeadMethods tests/test_instantly_client.py::TestEmailMethods tests/test_instantly_client.py::TestLeadListMethods -v`
Expected: FAIL

- [ ] **Step 3: Implement lead, email, and lead list methods**

Add to `InstantlyClient` in `tools/instantly_client.py`:

```python
    # -- Leads ------------------------------------------------------------

    async def create_lead(
        self,
        email: str,
        campaign_id: str,
        first_name: str = "",
        last_name: str = "",
        company_name: str = "",
        custom_variables: dict[str, str] | None = None,
    ) -> dict:
        """Add a single lead."""
        lead = InstantlyLead(
            email=email, first_name=first_name, last_name=last_name,
            company_name=company_name,
            custom_variables=custom_variables or {},
        )
        payload = lead.to_api_dict()
        payload["campaign_id"] = campaign_id
        return await self._request("POST", "/api/v2/leads", json=payload)

    async def add_leads_bulk(
        self, campaign_id: str, leads: list[InstantlyLead],
    ) -> dict:
        """Add up to 1000 leads in bulk."""
        payload = {
            "campaign_id": campaign_id,
            "leads": [lead.to_api_dict() for lead in leads],
        }
        return await self._request("POST", "/api/v2/leads/bulk-add", json=payload)

    async def list_leads(self, campaign_id: str, limit: int = 100) -> list[dict]:
        """List leads in a campaign."""
        data = await self._request(
            "POST", "/api/v2/leads/list",
            json={"campaign_id": campaign_id, "limit": limit},
        )
        return data.get("items", [])

    async def update_lead_interest(self, lead_id: str, status: str) -> dict:
        """Update a lead's interest status."""
        return await self._request(
            "PATCH", f"/api/v2/leads/{lead_id}/interest-status",
            json={"interest_status": status},
        )

    # -- Emails -----------------------------------------------------------

    async def list_emails(
        self,
        campaign_id: str | None = None,
        is_reply: bool | None = None,
        limit: int = 50,
    ) -> list[InstantlyEmail]:
        """List emails, optionally filtered by campaign and reply status."""
        params: dict[str, Any] = {"limit": limit}
        if campaign_id:
            params["campaign_id"] = campaign_id
        if is_reply is not None:
            params["is_reply"] = str(is_reply).lower()
        data = await self._request("GET", "/api/v2/emails", params=params)
        return [
            InstantlyEmail(
                id=e["id"],
                campaign_id=e.get("campaign_id", ""),
                lead_email=e.get("lead_email", ""),
                subject=e.get("subject", ""),
                body=e.get("body", ""),
                is_reply=e.get("is_reply", False),
                timestamp=e.get("timestamp", ""),
                thread_id=e.get("thread_id"),
            )
            for e in data.get("items", [])
        ]

    async def reply_to_email(
        self,
        email_id: str,
        campaign_id: str,
        body: str,
        thread_id: str | None = None,
    ) -> dict:
        """Send a reply to an email."""
        payload: dict[str, Any] = {
            "email_id": email_id,
            "campaign_id": campaign_id,
            "body": body,
        }
        if thread_id:
            payload["thread_id"] = thread_id
        return await self._request("POST", "/api/v2/emails/reply", json=payload)

    # -- Lead Lists -------------------------------------------------------

    async def create_lead_list(self, name: str) -> dict:
        """Create a named lead list."""
        return await self._request("POST", "/api/v2/lead-lists", json={"name": name})
```

- [ ] **Step 4: Run ALL client tests — expect PASS**

Run: `python3 -m pytest tests/test_instantly_client.py -v`
Expected: All passed (DTOs + core + campaigns + leads + emails + lead lists)

- [ ] **Step 5: Commit**

```bash
git add tools/instantly_client.py tests/test_instantly_client.py
git commit -m "feat(instantly): add lead, email, and lead list methods"
```

---

## Chunk 2: Pax Integration

### Task 4: Pax constructor + upload_leads()

**Files:**
- Modify: `agents/pax.py`
- Create: `tests/test_pax_instantly.py`

- [ ] **Step 1: Write tests for Pax constructor and upload_leads()**

```python
# tests/test_pax_instantly.py
"""Tests for Pax Instantly AI integration."""

import csv
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.pax import Pax
from tools.instantly_client import InstantlyLead


@pytest.fixture
def mock_instantly():
    client = MagicMock()
    client.add_leads_bulk = AsyncMock(return_value={"added": 3, "skipped": 0})
    client.create_campaign = AsyncMock(return_value=MagicMock(
        id="camp_1", name="Test", status="draft",
    ))
    client.list_emails = AsyncMock(return_value=[])
    client.close = AsyncMock()
    return client


@pytest.fixture
def pax_with_instantly(posthog_client, knowledge_base_path, mock_llm_client, mock_instantly):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        instantly_client=mock_instantly,
    )


class TestPaxUploadLeads:
    """Test lead upload from various sources."""

    @pytest.mark.asyncio
    async def test_upload_from_dict_list(self, pax_with_instantly, mock_instantly):
        leads = [
            {"email": "a@co.com", "first_name": "Alice"},
            {"email": "b@co.com", "first_name": "Bob"},
        ]
        result = await pax_with_instantly.upload_leads("camp_1", leads=leads)
        assert result["total_uploaded"] == 2
        mock_instantly.add_leads_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_from_csv(self, pax_with_instantly, mock_instantly, tmp_path):
        csv_file = tmp_path / "leads.csv"
        csv_file.write_text("email,first_name,last_name\na@co.com,Alice,A\nb@co.com,Bob,B\n")
        result = await pax_with_instantly.upload_leads("camp_1", csv_path=csv_file)
        assert result["total_uploaded"] == 2

    @pytest.mark.asyncio
    async def test_upload_batches_over_1000(self, pax_with_instantly, mock_instantly):
        leads = [{"email": f"user{i}@co.com"} for i in range(1500)]
        mock_instantly.add_leads_bulk = AsyncMock(
            side_effect=[{"added": 1000, "skipped": 0}, {"added": 500, "skipped": 0}]
        )
        result = await pax_with_instantly.upload_leads("camp_1", leads=leads)
        assert result["total_uploaded"] == 1500
        assert mock_instantly.add_leads_bulk.call_count == 2

    @pytest.mark.asyncio
    async def test_upload_no_source_returns_zero(self, pax_with_instantly):
        result = await pax_with_instantly.upload_leads("camp_1")
        assert result["total_uploaded"] == 0

    @pytest.mark.asyncio
    async def test_upload_from_context_issues(self, pax_with_instantly, mock_instantly):
        context = {
            "sage_triage": {
                "issues": [
                    {"author": "dev1", "author_email": "dev1@co.com"},
                    {"author": "dev2", "author_email": "dev2@co.com"},
                    {"author": "dev3"},  # no email — should be skipped
                ],
            },
        }
        result = await pax_with_instantly.upload_leads("camp_1", context=context)
        assert result["total_uploaded"] == 2
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python3 -m pytest tests/test_pax_instantly.py::TestPaxUploadLeads -v`
Expected: FAIL — `upload_leads` not found

- [ ] **Step 3: Implement constructor change and upload_leads()**

In `agents/pax.py`, add to imports:

```python
import csv
from tools.instantly_client import InstantlyClient, InstantlyLead
```

Modify `__init__`:

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    instantly_client: Optional["InstantlyClient"] = None,
    product_name: str = "the target product",
):
    # ...existing init...
    self.instantly_client = instantly_client
```

Add `upload_leads()` method:

```python
BULK_BATCH_SIZE = 1000

async def upload_leads(
    self,
    campaign_id: str,
    leads: list[dict] | None = None,
    csv_path: Path | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload leads to an Instantly campaign from various sources."""
    parsed_leads: list[InstantlyLead] = []

    # Source 1: Dict list
    if leads:
        for lead in leads:
            parsed_leads.append(InstantlyLead(
                email=lead.get("email", ""),
                first_name=lead.get("first_name", ""),
                last_name=lead.get("last_name", ""),
                company_name=lead.get("company_name", ""),
                title=lead.get("title", ""),
                custom_variables=lead.get("custom_variables", {}),
            ))

    # Source 2: CSV file
    if csv_path and csv_path.exists():
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                parsed_leads.append(InstantlyLead(
                    email=row.get("email", ""),
                    first_name=row.get("first_name", ""),
                    last_name=row.get("last_name", ""),
                    company_name=row.get("company_name", ""),
                    title=row.get("title", ""),
                ))

    # Source 3: Upstream context (GitHub issue authors)
    if context and "sage_triage" in context:
        issues = context["sage_triage"].get("issues", [])
        for issue in issues:
            email = issue.get("author_email")
            if email:
                parsed_leads.append(InstantlyLead(
                    email=email,
                    first_name=issue.get("author", ""),
                ))

    # Filter out leads without email
    parsed_leads = [l for l in parsed_leads if l.email]

    if not parsed_leads or not self.instantly_client:
        return {"total_uploaded": 0, "batches": 0, "errors": []}

    # Batch upload
    total_uploaded = 0
    errors: list[str] = []
    batches = 0
    for i in range(0, len(parsed_leads), self.BULK_BATCH_SIZE):
        batch = parsed_leads[i : i + self.BULK_BATCH_SIZE]
        try:
            result = await self.instantly_client.add_leads_bulk(campaign_id, batch)
            total_uploaded += result.get("added", len(batch))
            batches += 1
        except Exception as e:
            errors.append(str(e))
            logger.warning(f"Bulk upload batch {batches} failed: {e}")

    return {"total_uploaded": total_uploaded, "batches": batches, "errors": errors}
```

Add new asset types to `ASSET_KEYWORDS`:

```python
ASSET_KEYWORDS: dict[str, list[str]] = {
    # ...existing...
    "instantly_campaign": ["instantly", "cold email", "outreach campaign"],
    "lead_upload": ["upload leads", "import leads", "add leads"],
    "triage_replies": ["triage", "replies", "follow-up"],
}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_pax_instantly.py::TestPaxUploadLeads -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add agents/pax.py tests/test_pax_instantly.py
git commit -m "feat(pax): add Instantly client integration and upload_leads()"
```

---

### Task 5: Pax draft_followups() and triage_replies execute path

**Files:**
- Modify: `agents/pax.py`
- Modify: `tests/test_pax_instantly.py`

- [ ] **Step 1: Write tests for draft_followups and triage path**

Add to `tests/test_pax_instantly.py`:

```python
import json


class TestPaxDraftFollowups:
    """Test follow-up email drafting from triaged replies."""

    @pytest.mark.asyncio
    async def test_drafts_for_interested(self, pax_with_instantly, mock_llm_client):
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "subject": "Re: Great to hear from you",
            "body": "Thanks for your interest! Here's how to get started...",
        }))
        replies = [
            {"reply_id": "r1", "email_id": "e1", "category": "interested",
             "body": "Sounds interesting, tell me more", "lead_email": "a@co.com"},
        ]
        drafts = await pax_with_instantly.draft_followups(replies)
        assert len(drafts) == 1
        assert drafts[0]["category"] == "interested"
        assert drafts[0]["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_skips_unsubscribe_and_auto_reply(self, pax_with_instantly):
        replies = [
            {"reply_id": "r1", "email_id": "e1", "category": "unsubscribe",
             "body": "Please remove me", "lead_email": "a@co.com"},
            {"reply_id": "r2", "email_id": "e2", "category": "auto_reply",
             "body": "Out of office", "lead_email": "b@co.com"},
        ]
        drafts = await pax_with_instantly.draft_followups(replies)
        assert len(drafts) == 0

    @pytest.mark.asyncio
    async def test_drafts_for_objection(self, pax_with_instantly, mock_llm_client):
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "subject": "Re: Addressing your concern",
            "body": "I understand your concern about pricing...",
        }))
        replies = [
            {"reply_id": "r1", "email_id": "e1", "category": "objection",
             "body": "Too expensive for us", "lead_email": "a@co.com"},
        ]
        drafts = await pax_with_instantly.draft_followups(replies)
        assert len(drafts) == 1


class TestPaxTriageRepliesExecute:
    """Test the triage_replies path via execute()."""

    @pytest.mark.asyncio
    async def test_execute_triage_replies_type(self, pax_with_instantly):
        result = await pax_with_instantly.execute("Triage email replies and draft follow-ups")
        assert result["agent"] == "pax"
        assert result["asset_type"] == "triage_replies"
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python3 -m pytest tests/test_pax_instantly.py::TestPaxDraftFollowups tests/test_pax_instantly.py::TestPaxTriageRepliesExecute -v`
Expected: FAIL

- [ ] **Step 3: Implement draft_followups() and triage execute path**

Add to `agents/pax.py`:

```python
FOLLOWUP_CATEGORIES = {"interested", "objection"}

TRIAGE_PROMPT = """Classify this email reply into one of: interested, objection, not_now, unsubscribe, auto_reply.

Reply text:
---
{reply_body}
---

Return a JSON object: {{"category": "..."}}"""

FOLLOWUP_PROMPT = """Draft a follow-up email for this reply.

Category: {category}
Original reply: {reply_body}
Lead: {lead_email}

## Knowledge Base Context
{kb_context}

Write a personalized, non-salesy follow-up. Be helpful and specific.
Return JSON: {{"subject": "...", "body": "..."}}"""

async def draft_followups(
    self,
    replies: list[dict],
    context: dict[str, Any] | None = None,
) -> list[dict]:
    """Draft follow-up emails for interested/objection replies."""
    drafts = []
    actionable = [r for r in replies if r.get("category") in self.FOLLOWUP_CATEGORIES]

    if not actionable or not self.llm_client:
        return drafts

    kb_context = self._kb.search_as_text("outreach follow-up")

    for reply in actionable:
        try:
            raw = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT.format(product_name=self.product_name),
                user_prompt=self.FOLLOWUP_PROMPT.format(
                    category=reply["category"],
                    reply_body=reply.get("body", "")[:1000],
                    lead_email=reply.get("lead_email", ""),
                    kb_context=kb_context,
                ),
                temperature=0.5,
            )
            from agents.base import strip_markdown_fences
            data = json.loads(strip_markdown_fences(raw))
            drafts.append({
                "reply_id": reply.get("reply_id"),
                "email_id": reply.get("email_id"),
                "draft_subject": data.get("subject", ""),
                "draft_body": data.get("body", ""),
                "category": reply["category"],
                "status": "pending_approval",
            })
        except Exception as e:
            logger.warning(f"Failed to draft follow-up for {reply.get('reply_id')}: {e}")

    return drafts
```

Modify `execute()` to handle the new asset types. Add before the LLM generation block:

```python
# Handle Instantly-specific asset types
if asset_type == "instantly_campaign" and self.instantly_client and self.llm_client:
    # Generate email sequence via LLM, create campaign in Instantly
    prompt = (
        f"Create a cold email outreach campaign for {self.product_name}. "
        f"Return JSON: {{\"sequences\": [{{\"subject\": \"...\", \"body\": \"...\", \"delay_days\": N}}]}}"
    )
    raw = await self.llm_client.generate(
        system_prompt=self.SYSTEM_PROMPT.format(product_name=self.product_name),
        user_prompt=prompt,
    )
    from agents.base import strip_markdown_fences
    data = json.loads(strip_markdown_fences(raw))
    sequences = data.get("sequences", [])
    campaign = await self.instantly_client.create_campaign(
        name=f"{self.product_name} - Outreach",
        sequences=sequences,
    )
    return {
        "agent": "pax", "task": task, "asset_type": asset_type,
        "status": "campaign_created",
        "campaign_id": campaign.id, "campaign_name": campaign.name,
    }

if asset_type == "lead_upload" and self.instantly_client:
    return {
        "agent": "pax",
        "task": task,
        "asset_type": asset_type,
        "status": "uploaded",
        **(await self.upload_leads(
            campaign_id=context.get("campaign_id", "") if context else "",
            context=context,
        )),
    }

if asset_type == "triage_replies" and self.instantly_client:
    emails = await self.instantly_client.list_emails(is_reply=True)
    classified: list[dict] = []
    for email in emails:
        if self.llm_client:
            try:
                raw = await self.llm_client.generate(
                    system_prompt="You classify email replies.",
                    user_prompt=self.TRIAGE_PROMPT.format(reply_body=email.body[:1000]),
                    temperature=0.0,
                )
                from agents.base import strip_markdown_fences
                data = json.loads(strip_markdown_fences(raw))
                classified.append({
                    "reply_id": email.id,
                    "email_id": email.id,
                    "category": data.get("category", "not_now"),
                    "body": email.body,
                    "lead_email": email.lead_email,
                })
            except Exception:
                classified.append({
                    "reply_id": email.id, "email_id": email.id,
                    "category": "not_now", "body": email.body,
                    "lead_email": email.lead_email,
                })
    drafts = await self.draft_followups(classified, context)
    categories = {}
    for c in classified:
        categories[c["category"]] = categories.get(c["category"], 0) + 1
    return {
        "agent": "pax", "task": task, "asset_type": asset_type,
        "status": "triaged", "total_replies": len(classified),
        "categories": categories, "drafts": drafts,
    }
```

For the no-Instantly-client fallback, the existing code path already returns `prompt_used`.

- [ ] **Step 4: Run ALL Pax Instantly tests — expect PASS**

Run: `python3 -m pytest tests/test_pax_instantly.py -v`
Expected: All passed

- [ ] **Step 5: Run original Pax tests to confirm no regressions**

Run: `python3 -m pytest tests/test_pax.py -v`
Expected: All passed (existing tests still work since `instantly_client` defaults to `None`)

- [ ] **Step 6: Commit**

```bash
git add agents/pax.py tests/test_pax_instantly.py
git commit -m "feat(pax): add reply triage and follow-up drafting"
```

---

## Chunk 3: Mox Integration

### Task 6: Mox push_campaign() and pull_campaign_stats()

**Files:**
- Modify: `agents/mox.py`
- Create: `tests/test_mox_instantly.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_mox_instantly.py
"""Tests for Mox Instantly AI integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.mox import Mox
from tools.instantly_client import CampaignAnalytics, InstantlyCampaign


@pytest.fixture
def mock_instantly():
    client = MagicMock()
    client.create_campaign = AsyncMock(return_value=InstantlyCampaign(
        id="camp_1", name="Q1 Outreach", status="draft",
        accounts=["sender@co.com"], sequences=[],
    ))
    client.list_campaigns = AsyncMock(return_value=[
        InstantlyCampaign(id="c1", name="A", status="active", accounts=[], sequences=[]),
        InstantlyCampaign(id="c2", name="B", status="active", accounts=[], sequences=[]),
    ])
    client.get_campaign_analytics = AsyncMock(side_effect=[
        CampaignAnalytics(
            campaign_id="c1", campaign_name="A", total_leads=100,
            emails_sent=80, emails_opened=40, emails_replied=10,
            emails_bounced=5, open_rate=0.5, reply_rate=0.125, bounce_rate=0.0625,
        ),
        CampaignAnalytics(
            campaign_id="c2", campaign_name="B", total_leads=50,
            emails_sent=40, emails_opened=20, emails_replied=5,
            emails_bounced=2, open_rate=0.5, reply_rate=0.125, bounce_rate=0.05,
        ),
    ])
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
    async def test_push_campaign_no_client(self, posthog_client, knowledge_base_path, mock_llm_client):
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
        mock_instantly.get_campaign_analytics = AsyncMock(return_value=CampaignAnalytics(
            campaign_id="c1", campaign_name="A", total_leads=100,
            emails_sent=80, emails_opened=40, emails_replied=10,
            emails_bounced=5, open_rate=0.5, reply_rate=0.125, bounce_rate=0.0625,
        ))
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
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python3 -m pytest tests/test_mox_instantly.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Mox Instantly methods**

In `agents/mox.py`, add import:

```python
from tools.instantly_client import InstantlyClient, CampaignAnalytics
```

Modify `__init__` to accept `instantly_client`:

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    search_tools: Optional[SearchTools] = None,
    instantly_client: Optional["InstantlyClient"] = None,
    product_name: str = "the target product",
):
    # ...existing...
    self.instantly_client = instantly_client
```

Add `"email_campaign"` to `CONTENT_KEYWORDS`:

```python
CONTENT_KEYWORDS: dict[str, list[str]] = {
    # ...existing...
    "email_campaign": ["email campaign", "cold email", "drip campaign"],
}
```

Add methods:

```python
async def push_campaign(
    self,
    campaign_name: str,
    email_sequences: list[dict],
    accounts: list[str] | None = None,
) -> dict[str, Any]:
    """Create a campaign in Instantly with email sequences."""
    if not self.instantly_client:
        return {"error": "No Instantly client configured"}

    campaign = await self.instantly_client.create_campaign(
        name=campaign_name,
        sequences=email_sequences,
        accounts=accounts,
    )
    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "status": campaign.status,
    }

async def pull_campaign_stats(
    self,
    campaign_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch and aggregate analytics for active campaigns."""
    if not self.instantly_client:
        return {"total_campaigns": 0, "total_sent": 0, "total_opened": 0,
                "total_replied": 0, "total_bounced": 0, "avg_open_rate": 0.0,
                "avg_reply_rate": 0.0, "avg_bounce_rate": 0.0, "per_campaign": []}

    if campaign_ids:
        ids = campaign_ids
    else:
        campaigns = await self.instantly_client.list_campaigns()
        ids = [c.id for c in campaigns if c.status == "active"]

    analytics: list[CampaignAnalytics] = []
    for cid in ids:
        try:
            a = await self.instantly_client.get_campaign_analytics(cid)
            analytics.append(a)
        except Exception as e:
            logger.warning(f"Failed to get analytics for {cid}: {e}")

    if not analytics:
        return {"total_campaigns": 0, "total_sent": 0, "total_opened": 0,
                "total_replied": 0, "total_bounced": 0, "avg_open_rate": 0.0,
                "avg_reply_rate": 0.0, "avg_bounce_rate": 0.0, "per_campaign": []}

    total_sent = sum(a.emails_sent for a in analytics)
    total_opened = sum(a.emails_opened for a in analytics)
    total_replied = sum(a.emails_replied for a in analytics)
    total_bounced = sum(a.emails_bounced for a in analytics)
    n = len(analytics)

    return {
        "total_campaigns": n,
        "total_sent": total_sent,
        "total_opened": total_opened,
        "total_replied": total_replied,
        "total_bounced": total_bounced,
        "avg_open_rate": sum(a.open_rate for a in analytics) / n,
        "avg_reply_rate": sum(a.reply_rate for a in analytics) / n,
        "avg_bounce_rate": sum(a.bounce_rate for a in analytics) / n,
        "per_campaign": [
            {"campaign_id": a.campaign_id, "campaign_name": a.campaign_name,
             "emails_sent": a.emails_sent, "reply_rate": a.reply_rate}
            for a in analytics
        ],
    }
```

In `execute()`, add before the LLM generation block:

```python
if content_type == "email_campaign" and self.instantly_client and self.llm_client:
    # Generate email sequences via LLM
    prompt = (
        f"Create a cold email drip campaign for {self.product_name}. "
        f"Return JSON: {{\"sequences\": [{{\"subject\": \"...\", \"body\": \"...\", \"delay_days\": N}}]}}"
    )
    raw = await self.llm_client.generate(
        system_prompt=self.SYSTEM_PROMPT.format(product_name=self.product_name),
        user_prompt=prompt,
    )
    from agents.base import strip_markdown_fences
    data = json.loads(strip_markdown_fences(raw))
    sequences = data.get("sequences", [])

    # Push to Instantly
    campaign_result = await self.push_campaign(
        campaign_name=f"{self.product_name} - {task[:50]}",
        email_sequences=sequences,
    )
    return {
        "agent": "mox",
        "task": task,
        "content_type": content_type,
        "status": "campaign_created",
        **campaign_result,
    }
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_mox_instantly.py -v`
Expected: All passed

- [ ] **Step 5: Run original Mox tests for regressions**

Run: `python3 -m pytest tests/test_mox.py -v`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add agents/mox.py tests/test_mox_instantly.py
git commit -m "feat(mox): add push_campaign() and pull_campaign_stats()"
```

---

## Chunk 4: Atlas Integration + Config

### Task 7: SharedContext updates + load() classmethod

**Files:**
- Modify: `agents/atlas.py`
- Create: `tests/test_atlas_replies.py`

- [ ] **Step 1: Write tests for SharedContext changes**

```python
# tests/test_atlas_replies.py
"""Tests for Atlas Instantly integration — Stage 7 and review-replies."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.atlas import Atlas, SharedContext


class TestSharedContextInstantly:
    """Test new SharedContext fields and load()."""

    def test_new_fields_default_empty(self):
        ctx = SharedContext(week_of="2026-W12")
        assert ctx.instantly_campaigns == {}
        assert ctx.instantly_analytics == {}
        assert ctx.instantly_replies == {}

    def test_to_dict_includes_instantly_fields(self):
        ctx = SharedContext(
            week_of="2026-W12",
            instantly_analytics={"total_sent": 100},
        )
        d = ctx.to_dict()
        assert "instantly_campaigns" in d
        assert "instantly_analytics" in d
        assert "instantly_replies" in d
        assert d["instantly_analytics"]["total_sent"] == 100

    def test_save_includes_instantly_fields(self, tmp_path):
        ctx = SharedContext(
            week_of="2026-W12",
            instantly_analytics={"total_sent": 50},
        )
        ctx.save(tmp_path)
        saved = json.loads((tmp_path / "context_2026-W12.json").read_text())
        assert saved["instantly_analytics"]["total_sent"] == 50

    def test_load_most_recent(self, tmp_path):
        # Create two archived contexts
        (tmp_path / "context_2026-W11.json").write_text(json.dumps(
            {"week_of": "2026-W11", "sage_triage": {}, "echo_social": {},
             "iris_themes": {}, "nova_experiments": {}, "kai_content": {},
             "vox_video": {}, "dex_docs": {}, "rex_competitive": {},
             "pax_sales": {}, "mox_campaigns": {}, "okr_progress": {},
             "instantly_campaigns": {}, "instantly_analytics": {},
             "instantly_replies": {"drafts": [{"id": "old"}]}},
        ))
        (tmp_path / "context_2026-W12.json").write_text(json.dumps(
            {"week_of": "2026-W12", "sage_triage": {}, "echo_social": {},
             "iris_themes": {}, "nova_experiments": {}, "kai_content": {},
             "vox_video": {}, "dex_docs": {}, "rex_competitive": {},
             "pax_sales": {}, "mox_campaigns": {}, "okr_progress": {},
             "instantly_campaigns": {}, "instantly_analytics": {},
             "instantly_replies": {"drafts": [{"id": "new"}]}},
        ))
        ctx = SharedContext.load(tmp_path)
        assert ctx.week_of == "2026-W12"
        assert ctx.instantly_replies["drafts"][0]["id"] == "new"

    def test_load_empty_dir(self, tmp_path):
        ctx = SharedContext.load(tmp_path)
        assert ctx.week_of != ""  # defaults to current week
        assert ctx.instantly_replies == {}

    def test_load_nonexistent_dir(self, tmp_path):
        ctx = SharedContext.load(tmp_path / "nonexistent")
        assert ctx.instantly_replies == {}
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python3 -m pytest tests/test_atlas_replies.py::TestSharedContextInstantly -v`
Expected: FAIL — missing fields / missing `load()`

- [ ] **Step 3: Implement SharedContext changes**

In `agents/atlas.py`, update `SharedContext`:

Add 3 new fields after `mox_campaigns`:

```python
instantly_campaigns: dict[str, Any] = field(default_factory=dict)
instantly_analytics: dict[str, Any] = field(default_factory=dict)
instantly_replies: dict[str, Any] = field(default_factory=dict)
```

Update `to_dict()` to include them:

```python
"instantly_campaigns": self.instantly_campaigns,
"instantly_analytics": self.instantly_analytics,
"instantly_replies": self.instantly_replies,
```

Add `load()` classmethod:

```python
@classmethod
def load(cls, archive_dir: Path) -> "SharedContext":
    """Load the most recent archived context."""
    ctx = cls(week_of=datetime.now().strftime("%Y-W%U"))
    if not archive_dir.exists():
        return ctx
    files = sorted(archive_dir.glob("context_*.json"), reverse=True)
    if not files:
        return ctx
    try:
        data = json.loads(files[0].read_text())
        for key, value in data.items():
            if hasattr(ctx, key):
                setattr(ctx, key, value)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load context from {files[0]}: {e}")
    return ctx
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_atlas_replies.py::TestSharedContextInstantly -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add agents/atlas.py tests/test_atlas_replies.py
git commit -m "feat(atlas): add Instantly SharedContext fields and load() classmethod"
```

---

### Task 8: Atlas Stage 7 + OKR updates + wiring

**Files:**
- Modify: `agents/atlas.py`
- Modify: `tests/test_atlas_replies.py`

- [ ] **Step 1: Write tests for Stage 7 and OKR**

Add to `tests/test_atlas_replies.py`:

```python
class TestAtlasStage7:
    """Test Stage 7 Instantly sync in weekly cycle."""

    @pytest.mark.asyncio
    async def test_stage7_delegates_to_mox_and_pax(
        self, posthog_client, knowledge_base_path, mock_llm_client, tmp_path,
    ):
        mock_instantly = MagicMock()
        mock_instantly.close = AsyncMock()

        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            instantly_client=mock_instantly,
            archive_dir=tmp_path / "archive",
        )
        atlas.BASE_DELAY = 0.001

        # Mock all agent executes to succeed
        for agent in atlas._agents.values():
            agent.execute = AsyncMock(return_value={
                "agent": "mock", "status": "ok",
            })

        await atlas.run_weekly_cycle()

        # Verify Mox was asked for analytics and Pax for triage
        mox_calls = [
            c for c in atlas.mox.execute.call_args_list
            if "analytics" in str(c).lower() or "instantly" in str(c).lower()
        ]
        pax_calls = [
            c for c in atlas.pax.execute.call_args_list
            if "triage" in str(c).lower() or "replies" in str(c).lower()
        ]
        assert len(mox_calls) >= 1, "Mox should be delegated analytics pull"
        assert len(pax_calls) >= 1, "Pax should be delegated reply triage"

    @pytest.mark.asyncio
    async def test_stage7_skipped_without_client(
        self, posthog_client, knowledge_base_path, mock_llm_client, tmp_path,
    ):
        atlas = Atlas(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            archive_dir=tmp_path / "archive",
        )
        atlas.BASE_DELAY = 0.001

        for agent in atlas._agents.values():
            agent.execute = AsyncMock(return_value={"agent": "mock", "status": "ok"})

        ctx = await atlas.run_weekly_cycle()
        assert ctx.instantly_analytics == {}


class TestAtlasOKRInstantly:
    """Test OKR compilation includes Instantly metrics."""

    def test_okr_includes_email_metrics(self):
        ctx = SharedContext(
            week_of="2026-W12",
            instantly_analytics={"total_sent": 100, "total_opened": 50,
                                 "total_replied": 10, "avg_reply_rate": 0.1},
            instantly_replies={"drafts": [{"id": "d1"}, {"id": "d2"}]},
        )
        atlas = Atlas.__new__(Atlas)
        atlas.context = ctx
        okrs = atlas._compile_okrs()
        assert okrs["emails_sent"] == 100
        assert okrs["emails_replied"] == 10
        assert okrs["followups_pending"] == 2
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python3 -m pytest tests/test_atlas_replies.py::TestAtlasStage7 tests/test_atlas_replies.py::TestAtlasOKRInstantly -v`
Expected: FAIL

- [ ] **Step 3: Implement Stage 7 + OKR + wiring**

In `agents/atlas.py`:

Add import:
```python
from tools.instantly_client import InstantlyClient
```

Modify `__init__` to accept `instantly_client`:

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    archive_dir: Path = Path("context_archive"),
    llm_client: Optional[LLMClient] = None,
    github_tools: Optional[GitHubTools] = None,
    search_tools: Optional[SearchTools] = None,
    config: Optional[AgentConfig] = None,
    instantly_client: Optional[InstantlyClient] = None,
):
    # ...existing init...
    self.instantly_client = instantly_client
```

Pass `instantly_client` to Pax and Mox in their constructors:

```python
self.pax = Pax(
    api_client=api_client,
    knowledge_base_path=knowledge_base_path,
    llm_client=llm_client,
    instantly_client=instantly_client,
)
self.mox = Mox(
    api_client=api_client,
    knowledge_base_path=knowledge_base_path,
    llm_client=llm_client,
    search_tools=search_tools,
    instantly_client=instantly_client,
)
```

Add Stage 7 to `run_weekly_cycle()` after Stage 6 (Vox):

```python
# Stage 7: Instantly sync (analytics + reply triage)
if self.instantly_client:
    analytics_result = await self.delegate(
        "mox",
        "Pull campaign analytics from Instantly for all active campaigns.",
    )
    if analytics_result.success:
        self.context.instantly_analytics = analytics_result.output

    triage_result = await self.delegate(
        "pax",
        "Fetch new email replies from Instantly, triage them, and draft follow-ups for interested leads.",
    )
    if triage_result.success:
        self.context.instantly_replies = triage_result.output
```

Update `_compile_okrs()`:

```python
"emails_sent": self.context.instantly_analytics.get("total_sent", 0),
"emails_opened": self.context.instantly_analytics.get("total_opened", 0),
"emails_replied": self.context.instantly_analytics.get("total_replied", 0),
"reply_rate": self.context.instantly_analytics.get("avg_reply_rate", 0),
"followups_pending": len(self.context.instantly_replies.get("drafts", [])),
```

Update `main()` to create InstantlyClient and clean it up:

```python
# After search tools creation:
instantly_client = (
    InstantlyClient(api_key=os.environ.get("INSTANTLY_API_KEY", ""))
    if os.environ.get("INSTANTLY_API_KEY")
    else None
)

# Pass to Atlas:
atlas = Atlas(
    api_client=client,
    knowledge_base_path=kb_path,
    llm_client=llm_client,
    github_tools=github_tools,
    search_tools=search,
    config=config,
    instantly_client=instantly_client,
)

# In finally block:
if instantly_client:
    await instantly_client.close()
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_atlas_replies.py -v`
Expected: All passed

- [ ] **Step 5: Run full test suite for regressions**

Run: `python3 -m pytest tests/ -q --no-header`
Expected: All passed, coverage ≥ 80%

- [ ] **Step 6: Commit**

```bash
git add agents/atlas.py tests/test_atlas_replies.py
git commit -m "feat(atlas): add Stage 7 Instantly sync, OKR metrics, and client wiring"
```

---

### Task 9: `--review-replies` CLI command

**Files:**
- Modify: `agents/atlas.py`
- Modify: `tests/test_atlas_replies.py`

- [ ] **Step 1: Write tests for the review-replies CLI**

Add to `tests/test_atlas_replies.py`:

```python
from unittest.mock import patch


class TestReviewRepliesCLI:
    """Test --review-replies interactive flow."""

    def test_argparse_accepts_review_replies(self):
        """Verify the CLI argument is registered."""
        from agents.atlas import build_parser
        parser = build_parser()
        args = parser.parse_args(["--review-replies"])
        assert args.review_replies is True

    @pytest.mark.asyncio
    async def test_review_replies_loads_context(self, tmp_path):
        import json
        # Create archived context with pending drafts
        (tmp_path / "context_2026-W12.json").write_text(json.dumps({
            "week_of": "2026-W12", "sage_triage": {}, "echo_social": {},
            "iris_themes": {}, "nova_experiments": {}, "kai_content": {},
            "vox_video": {}, "dex_docs": {}, "rex_competitive": {},
            "pax_sales": {}, "mox_campaigns": {}, "okr_progress": {},
            "instantly_campaigns": {}, "instantly_analytics": {},
            "instantly_replies": {
                "drafts": [
                    {"reply_id": "r1", "email_id": "e1", "draft_subject": "Re: Hello",
                     "draft_body": "Thanks for your interest!", "category": "interested",
                     "status": "pending_approval"},
                ]
            },
        }))
        ctx = SharedContext.load(tmp_path)
        drafts = ctx.instantly_replies.get("drafts", [])
        pending = [d for d in drafts if d.get("status") == "pending_approval"]
        assert len(pending) == 1

    @pytest.mark.asyncio
    async def test_review_approve_sends_reply(self):
        mock_instantly = MagicMock()
        mock_instantly.reply_to_email = AsyncMock(return_value={"status": "sent"})

        draft = {
            "reply_id": "r1", "email_id": "e1",
            "draft_subject": "Re: Hello", "draft_body": "Thanks!",
            "category": "interested", "status": "pending_approval",
        }

        # Simulate approval
        with patch("builtins.input", return_value="a"):
            from agents.atlas import process_draft
            result = await process_draft(draft, mock_instantly)
            assert result == "approved"
            mock_instantly.reply_to_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_skip_leaves_pending(self):
        mock_instantly = MagicMock()

        draft = {
            "reply_id": "r1", "email_id": "e1",
            "draft_subject": "Re: Hello", "draft_body": "Thanks!",
            "category": "interested", "status": "pending_approval",
        }

        with patch("builtins.input", return_value="s"):
            from agents.atlas import process_draft
            result = await process_draft(draft, mock_instantly)
            assert result == "skipped"
            mock_instantly.reply_to_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_reject_discards(self):
        mock_instantly = MagicMock()

        draft = {
            "reply_id": "r1", "email_id": "e1",
            "draft_subject": "Re: Hello", "draft_body": "Thanks!",
            "category": "interested", "status": "pending_approval",
        }

        with patch("builtins.input", return_value="r"):
            from agents.atlas import process_draft
            result = await process_draft(draft, mock_instantly)
            assert result == "rejected"
            mock_instantly.reply_to_email.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failure**

Run: `python3 -m pytest tests/test_atlas_replies.py::TestReviewRepliesCLI -v`
Expected: FAIL — missing `build_parser`, `process_draft`

- [ ] **Step 3: Implement --review-replies CLI**

In `agents/atlas.py`, add the `build_parser()` function (or modify existing `main()` argparse):

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atlas Orchestrator")
    parser.add_argument("--weekly-cycle", action="store_true", help="Run full weekly cycle")
    parser.add_argument("--agent", type=str, help="Run a specific agent")
    parser.add_argument("--task", type=str, help="Task for the agent")
    parser.add_argument("--review-replies", action="store_true",
                        help="Review and approve pending follow-up email drafts")
    return parser
```

Add `process_draft()` function:

```python
async def process_draft(draft: dict, instantly_client) -> str:
    """Process a single follow-up draft interactively.

    Returns: 'approved', 'edited', 'skipped', or 'rejected'
    """
    print(f"\n{'='*60}")
    print(f"Category: {draft.get('category', 'unknown')}")
    print(f"To: {draft.get('lead_email', 'unknown')}")
    print(f"Subject: {draft.get('draft_subject', '')}")
    print(f"\n{draft.get('draft_body', '')}")
    print(f"{'='*60}")

    choice = input("[a]pprove / [e]dit / [s]kip / [r]eject: ").strip().lower()

    if choice == "a":
        await instantly_client.reply_to_email(
            email_id=draft["email_id"],
            campaign_id=draft.get("campaign_id", ""),
            body=draft["draft_body"],
            thread_id=draft.get("thread_id"),
        )
        draft["status"] = "sent"
        return "approved"
    elif choice == "e":
        import os, tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(draft["draft_body"])
            tmp_path = f.name
        editor = os.environ.get("EDITOR", "vi")
        os.system(f"{editor} {tmp_path}")
        with open(tmp_path) as f:
            edited_body = f.read()
        os.unlink(tmp_path)
        await instantly_client.reply_to_email(
            email_id=draft["email_id"],
            campaign_id=draft.get("campaign_id", ""),
            body=edited_body,
            thread_id=draft.get("thread_id"),
        )
        draft["status"] = "sent"
        return "edited"
    elif choice == "r":
        draft["status"] = "rejected"
        return "rejected"
    else:  # skip
        return "skipped"
```

Add the review-replies handler in `main()`:

```python
if args.review_replies:
    archive_dir = Path("context_archive")
    ctx = SharedContext.load(archive_dir)
    drafts = ctx.instantly_replies.get("drafts", [])
    pending = [d for d in drafts if d.get("status") == "pending_approval"]

    if not pending:
        print("No pending follow-up drafts to review.")
        return

    if not instantly_client:
        print("Error: INSTANTLY_API_KEY not set. Cannot send replies.")
        return

    print(f"\n{len(pending)} pending follow-up(s) to review:\n")
    stats = {"approved": 0, "edited": 0, "skipped": 0, "rejected": 0}

    for draft in pending:
        result = await process_draft(draft, instantly_client)
        stats[result] = stats.get(result, 0) + 1

    print(f"\nDone! {stats['approved']} approved, {stats['edited']} edited, "
          f"{stats['skipped']} skipped, {stats['rejected']} rejected")
    return
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest tests/test_atlas_replies.py::TestReviewRepliesCLI -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add agents/atlas.py tests/test_atlas_replies.py
git commit -m "feat(atlas): add --review-replies CLI for human-in-the-loop approval"
```

---

## Chunk 5: Types, Config, and Final Verification

### Task 10: TypedDicts + config updates

**Files:**
- Modify: `agents/types.py`
- Modify: `config/env.example`
- Modify: `config/agent_config.yaml`

- [ ] **Step 1: Add TypedDicts to agents/types.py**

```python
class InstantlyAnalyticsResult(TypedDict):
    agent: str
    status: str
    total_campaigns: int
    total_sent: int
    total_opened: int
    total_replied: int
    total_bounced: int
    avg_open_rate: float
    avg_reply_rate: float
    avg_bounce_rate: float
    per_campaign: list[dict]


class InstantlyRepliesResult(TypedDict):
    agent: str
    status: str
    total_replies: int
    categories: dict
    drafts: list[dict]
```

- [ ] **Step 2: Add INSTANTLY_API_KEY to config/env.example**

Append:

```
# Instantly AI Configuration
INSTANTLY_API_KEY=your_instantly_api_key_here
```

- [ ] **Step 3: Add instantly section to config/agent_config.yaml**

Under `api_clients:`, add:

```yaml
  instantly:
    base_url: https://api.instantly.ai
    rate_limit_rpm: 50
    bulk_batch_size: 1000
    reply_check_enabled: true
```

- [ ] **Step 4: Commit**

```bash
git add agents/types.py config/env.example config/agent_config.yaml
git commit -m "feat: add Instantly TypedDicts and config entries"
```

---

### Task 11: Final verification

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v --no-header`
Expected: All passed, coverage ≥ 80%

- [ ] **Step 2: Run ruff lint on changed files**

Run: `python3 -m ruff check tools/instantly_client.py agents/pax.py agents/mox.py agents/atlas.py agents/types.py tests/test_instantly_client.py tests/test_pax_instantly.py tests/test_mox_instantly.py tests/test_atlas_replies.py`
Expected: No errors (fix any that appear)

- [ ] **Step 3: Verify no import cycles**

Run: `python3 -c "from tools.instantly_client import InstantlyClient; from agents.pax import Pax; from agents.mox import Mox; from agents.atlas import Atlas; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -u
git commit -m "chore: fix lint issues from Instantly integration"
```
