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

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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
            d["custom_variables"] = self.custom_variables
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


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


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
        campaign_schedule: dict | None = None,
    ) -> InstantlyCampaign:
        """Create a new campaign."""
        payload: dict[str, Any] = {"name": name, "sequences": sequences}
        if accounts:
            payload["email_list"] = accounts
        # Instantly v2 requires campaign_schedule
        payload["campaign_schedule"] = campaign_schedule or {
            "schedules": [{
                "name": "Default",
                "days": {
                    "0": True, "1": True, "2": True,
                    "3": True, "4": True, "5": False, "6": False,
                },
                "timezone": "Asia/Jerusalem",
                "timing": {"from": "09:00", "to": "17:00"},
            }],
        }
        data = await self._request("POST", "/api/v2/campaigns", json=payload)
        return InstantlyCampaign(
            id=data["id"],
            name=data["name"],
            status=data.get("status", "draft"),
            accounts=data.get("accounts", []),
            sequences=data.get("sequences", []),
        )

    async def get_campaign(self, campaign_id: str) -> InstantlyCampaign:
        """Get campaign details by ID."""
        data = await self._request("GET", f"/api/v2/campaigns/{campaign_id}")
        return InstantlyCampaign(
            id=data["id"],
            name=data["name"],
            status=data.get("status", ""),
            accounts=data.get("accounts", []),
            sequences=data.get("sequences", []),
        )

    async def list_campaigns(
        self,
        limit: int = 100,
        skip: int = 0,
    ) -> list[InstantlyCampaign]:
        """List campaigns with pagination."""
        data = await self._request(
            "GET", "/api/v2/campaigns", params={"limit": limit, "skip": skip},
        )
        return [
            InstantlyCampaign(
                id=c["id"],
                name=c["name"],
                status=c.get("status", ""),
                accounts=c.get("accounts", []),
                sequences=c.get("sequences", []),
            )
            for c in data.get("items", data if isinstance(data, list) else [])
        ]

    async def activate_campaign(self, campaign_id: str) -> dict:
        """Activate/resume a campaign."""
        return await self._request(
            "POST", f"/api/v2/campaigns/{campaign_id}/activate",
        )

    async def stop_campaign(self, campaign_id: str) -> dict:
        """Stop/pause a campaign."""
        return await self._request(
            "POST", f"/api/v2/campaigns/{campaign_id}/stop",
        )

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
            email=email,
            first_name=first_name,
            last_name=last_name,
            company_name=company_name,
            custom_variables=custom_variables or {},
        )
        payload = lead.to_api_dict()
        payload["campaign"] = campaign_id
        return await self._request("POST", "/api/v2/leads", json=payload)

    async def add_leads_bulk(
        self, campaign_id: str, leads: list[InstantlyLead],
        concurrency: int = 10,
    ) -> dict:
        """Add leads to a campaign and ensure custom variables are set.

        Instantly deduplicates by email at the org level, so re-adding an
        existing lead won't update its payload.  After the POST we PATCH
        custom_variables onto the returned (or existing) lead record.

        Processes leads in parallel batches of *concurrency* to respect
        API rate limits while avoiding sequential bottlenecks.
        """
        added = 0
        errors: list[str] = []
        semaphore = asyncio.Semaphore(concurrency)

        async def _add_one(lead: InstantlyLead) -> bool:
            async with semaphore:
                payload = lead.to_api_dict()
                payload["campaign"] = campaign_id
                try:
                    data = await self._request("POST", "/api/v2/leads", json=payload)
                    lead_id = data.get("id")
                    if lead_id and lead.custom_variables:
                        try:
                            await self._request(
                                "PATCH", f"/api/v2/leads/{lead_id}",
                                json={"custom_variables": lead.custom_variables},
                            )
                        except Exception as patch_exc:
                            logger.debug("PATCH vars failed for %s: %s", lead.email, patch_exc)
                    return True
                except Exception as exc:
                    errors.append(f"{lead.email}: {exc}")
                    logger.debug("Failed to add lead %s: %s", lead.email, exc)
                    return False

        results = await asyncio.gather(*[_add_one(lead) for lead in leads])
        added = sum(1 for r in results if r)
        return {"added": added, "errors": errors, "total": len(leads)}

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
        return await self._request(
            "POST", "/api/v2/lead-lists", json={"name": name},
        )
