# tools/apollo_client.py
"""
Apollo.io API async client.

Provides typed async access to Apollo's REST API for:
- People search (find contacts by title, company, industry)
- Organization search (find companies by criteria)
- Person enrichment (enrich by email or LinkedIn URL)
- Organization enrichment (enrich by domain)

Authentication: x-api-key header.
Rate limits: ~50 RPM standard plan; handled via tenacity retry on 429.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApolloAPIError(Exception):
    """Non-retryable error from the Apollo API (4xx except 429)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Apollo API error {status_code}: {detail}")


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------


@dataclass
class ApolloContact:
    """A contact/person from Apollo."""

    id: str
    first_name: str
    last_name: str
    email: str | None = None
    title: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None

    def to_instantly_lead(self) -> "InstantlyLead":
        """Convert to InstantlyLead for Instantly upload.

        Field mapping:
        - email -> email (empty string if None)
        - first_name -> first_name
        - last_name -> last_name
        - company_name -> company_name
        - phone, linkedin_url, title -> custom_variables (only if set)
        """
        from tools.instantly_client import InstantlyLead

        return InstantlyLead(
            email=self.email or "",
            first_name=self.first_name,
            last_name=self.last_name,
            company_name=self.company_name or "",
            custom_variables={
                k: v
                for k, v in {
                    "phone": self.phone,
                    "linkedin_url": self.linkedin_url,
                    "title": self.title,
                }.items()
                if v
            },
        )


@dataclass
class ApolloOrganization:
    """An organization from Apollo."""

    id: str
    name: str
    domain: str | None = None
    industry: str | None = None
    estimated_headcount: int | None = None
    tech_stack: list[str] = field(default_factory=list)
    funding_stage: str | None = None
    funding_total: float | None = None
    description: str | None = None
    linkedin_url: str | None = None


@dataclass
class PeopleSearchResult:
    """Result from people search endpoint."""

    contacts: list[ApolloContact]
    total: int
    page: int
    per_page: int


@dataclass
class OrgSearchResult:
    """Result from organization search endpoint."""

    organizations: list[ApolloOrganization]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ApolloClient:
    """Async client for the Apollo.io REST API."""

    BASE_URL = "https://api.apollo.io/v1"

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ApolloClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 429:
            raise httpx.HTTPStatusError(
                "Rate limited", request=response.request, response=response
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except Exception:
                detail = response.text
            raise ApolloAPIError(status_code=response.status_code, detail=detail)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=payload)
        self._raise_for_status(response)
        return response.json()

    @staticmethod
    def _parse_contact(data: dict[str, Any]) -> ApolloContact:
        return ApolloContact(
            id=data.get("id", ""),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            email=data.get("email"),
            title=data.get("title"),
            company_name=data.get("organization_name") or data.get("company_name"),
            company_domain=data.get("organization", {}).get("primary_domain")
            if isinstance(data.get("organization"), dict)
            else data.get("company_domain"),
            linkedin_url=data.get("linkedin_url"),
            phone=data.get("sanitized_phone") or data.get("phone"),
        )

    @staticmethod
    def _parse_organization(data: dict[str, Any]) -> ApolloOrganization:
        tech = data.get("technologies") or data.get("tech_stack") or []
        if isinstance(tech, list):
            tech_names = [t if isinstance(t, str) else t.get("name", "") for t in tech]
        else:
            tech_names = []

        return ApolloOrganization(
            id=data.get("id", ""),
            name=data.get("name", ""),
            domain=data.get("primary_domain") or data.get("domain"),
            industry=data.get("industry"),
            estimated_headcount=data.get("estimated_num_employees"),
            tech_stack=tech_names,
            funding_stage=data.get("latest_funding_stage") or data.get("funding_stage"),
            funding_total=data.get("total_funding"),
            description=data.get("short_description") or data.get("description"),
            linkedin_url=data.get("linkedin_url"),
        )

    async def search_people(
        self,
        *,
        titles: list[str] | None = None,
        domains: list[str] | None = None,
        industries: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
        **extra: Any,
    ) -> PeopleSearchResult:
        """Search for people by title, domain, or industry."""
        payload: dict[str, Any] = {"page": page, "per_page": min(per_page, 100)}
        if titles:
            payload["person_titles"] = titles
        if domains:
            payload["q_organization_domains"] = domains
        if industries:
            payload["organization_industry_tag_ids"] = industries
        payload.update(extra)

        data = await self._post("/mixed_people/api_search", payload)
        contacts = [self._parse_contact(c) for c in data.get("people", [])]
        pagination = data.get("pagination", {})
        return PeopleSearchResult(
            contacts=contacts,
            total=pagination.get("total_entries", data.get("total_entries", len(contacts))),
            page=pagination.get("page", page),
            per_page=pagination.get("per_page", per_page),
        )

    async def search_organizations(
        self,
        *,
        industries: list[str] | None = None,
        min_headcount: int | None = None,
        max_headcount: int | None = None,
        page: int = 1,
        per_page: int = 25,
        **extra: Any,
    ) -> OrgSearchResult:
        """Search for organizations by industry and headcount range."""
        payload: dict[str, Any] = {"page": page, "per_page": per_page}
        if industries:
            payload["organization_industry_tag_ids"] = industries
        if min_headcount is not None or max_headcount is not None:
            payload["organization_num_employees_ranges"] = [
                f"{min_headcount or 1},{max_headcount or 100_000}"
            ]
        payload.update(extra)

        data = await self._post("/mixed_companies/search", payload)
        orgs = [self._parse_organization(o) for o in data.get("organizations", [])]
        pagination = data.get("pagination", {})
        return OrgSearchResult(
            organizations=orgs,
            total=pagination.get("total_entries", len(orgs)),
            page=pagination.get("page", page),
            per_page=pagination.get("per_page", per_page),
        )

    async def enrich_person(
        self,
        *,
        person_id: str | None = None,
        email: str | None = None,
        linkedin_url: str | None = None,
    ) -> ApolloContact | None:
        """Enrich a person by ID, email, or LinkedIn URL. Returns None if not found."""
        if not person_id and not email and not linkedin_url:
            raise ValueError("Provide at least one of person_id, email, or linkedin_url")
        payload: dict[str, Any] = {}
        if person_id:
            payload["id"] = person_id
        if email:
            payload["email"] = email
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url

        data = await self._post("/people/match", payload)
        person = data.get("person")
        if not person:
            return None
        return self._parse_contact(person)

    async def enrich_organization(self, *, domain: str) -> ApolloOrganization | None:
        """Enrich an organization by domain. Returns None if not found."""
        data = await self._post("/organizations/enrich", {"domain": domain})
        org = data.get("organization")
        if not org:
            return None
        return self._parse_organization(org)
