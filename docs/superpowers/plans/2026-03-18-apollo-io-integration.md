# Apollo.io Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Apollo.io's Prospecting and Enrichment APIs into Pax (sales enablement) and Rex (competitive intelligence) agents.

**Architecture:** Thin async API client in `tools/apollo_client.py` (httpx + tenacity retry), injected into Pax and Rex via constructor, wired by Atlas. Apollo enriches leads before Instantly upload (Pax) and enriches competitor profiles with firmographic data (Rex).

**Tech Stack:** Python 3.12+, httpx, tenacity, pytest + pytest-asyncio + respx

**Spec:** `docs/superpowers/specs/2026-03-18-apollo-io-integration-design.md`

---

## Chunk 1: Apollo API Client

### File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `tools/apollo_client.py` | Async Apollo.io API client with DTOs |
| Create | `tests/test_apollo_client.py` | Client unit tests with respx mocks |
| Create | `tests/fixtures/apollo_sample_responses.json` | Canned API responses |

---

### Task 1: Apollo DTOs and Error Class

**Files:**
- Create: `tools/apollo_client.py`
- Create: `tests/test_apollo_client.py`

- [ ] **Step 1: Write failing tests for DTOs**

```python
# tests/test_apollo_client.py
"""Tests for Apollo.io API client."""

import httpx
import pytest
import respx

from tools.apollo_client import (
    ApolloAPIError,
    ApolloClient,
    ApolloContact,
    ApolloOrganization,
    OrgSearchResult,
    PeopleSearchResult,
)


class TestApolloDTOs:
    """Test dataclass creation and serialization."""

    def test_contact_defaults(self):
        contact = ApolloContact(
            id="apl_001", first_name="Jane", last_name="Smith",
        )
        assert contact.email is None
        assert contact.title is None
        assert contact.phone is None

    def test_contact_full(self):
        contact = ApolloContact(
            id="apl_001", first_name="Jane", last_name="Smith",
            email="jane@acme.com", title="VP Engineering",
            company_name="Acme Corp", company_domain="acme.com",
            linkedin_url="https://linkedin.com/in/janesmith",
            phone="+1234567890",
        )
        assert contact.email == "jane@acme.com"
        assert contact.company_domain == "acme.com"

    def test_contact_to_instantly_lead(self):
        contact = ApolloContact(
            id="apl_001", first_name="Jane", last_name="Smith",
            email="jane@acme.com", title="VP Engineering",
            company_name="Acme Corp", company_domain="acme.com",
            linkedin_url="https://linkedin.com/in/janesmith",
            phone="+1234567890",
        )
        lead = contact.to_instantly_lead()
        assert lead.email == "jane@acme.com"
        assert lead.first_name == "Jane"
        assert lead.last_name == "Smith"
        assert lead.company_name == "Acme Corp"
        assert lead.custom_variables["phone"] == "+1234567890"
        assert lead.custom_variables["linkedin_url"] == "https://linkedin.com/in/janesmith"
        assert lead.custom_variables["title"] == "VP Engineering"

    def test_contact_to_instantly_lead_sparse(self):
        contact = ApolloContact(
            id="apl_002", first_name="John", last_name="Doe",
            email="john@beta.io",
        )
        lead = contact.to_instantly_lead()
        assert lead.email == "john@beta.io"
        assert lead.company_name == ""
        assert lead.custom_variables == {}

    def test_contact_to_instantly_lead_no_email(self):
        contact = ApolloContact(
            id="apl_003", first_name="No", last_name="Email",
        )
        lead = contact.to_instantly_lead()
        assert lead.email == ""

    def test_organization_defaults(self):
        org = ApolloOrganization(
            id="org_001", name="Acme Corp",
        )
        assert org.domain is None
        assert org.tech_stack == []
        assert org.funding_total is None

    def test_organization_full(self):
        org = ApolloOrganization(
            id="org_001", name="Acme Corp", domain="acme.com",
            industry="Software", estimated_headcount=500,
            tech_stack=["React", "PostgreSQL"],
            funding_stage="Series B", funding_total=45_000_000.0,
            description="Developer tools company",
            linkedin_url="https://linkedin.com/company/acme",
        )
        assert org.tech_stack == ["React", "PostgreSQL"]
        assert org.funding_total == 45_000_000.0

    def test_people_search_result(self):
        result = PeopleSearchResult(
            contacts=[
                ApolloContact(id="a1", first_name="A", last_name="B"),
            ],
            total=100, page=1, per_page=25,
        )
        assert len(result.contacts) == 1
        assert result.total == 100

    def test_org_search_result(self):
        result = OrgSearchResult(
            organizations=[
                ApolloOrganization(id="o1", name="Org1"),
            ],
            total=50, page=1, per_page=25,
        )
        assert len(result.organizations) == 1

    def test_api_error(self):
        err = ApolloAPIError(status_code=401, detail="Invalid API key")
        assert err.status_code == 401
        assert "401" in str(err)
        assert "Invalid API key" in str(err)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_apollo_client.py -v`
Expected: FAIL with ModuleNotFoundError (tools.apollo_client doesn't exist yet)

- [ ] **Step 3: Write DTOs and error class**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestApolloDTOs -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add tools/apollo_client.py tests/test_apollo_client.py
git commit -m "feat(apollo): add DTOs and error class for Apollo.io client"
```

---

### Task 2: Apollo Client Core (_request method)

**Files:**
- Modify: `tools/apollo_client.py`
- Modify: `tests/test_apollo_client.py`

- [ ] **Step 1: Write failing tests for _request**

Add to `tests/test_apollo_client.py`:

```python
@pytest.fixture
def apollo_client():
    return ApolloClient(api_key="test-apollo-key", base_url="https://api.apollo.io")


class TestApolloClientRequest:
    """Test the core _request method."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_post(self, apollo_client):
        respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(200, json={"people": [], "pagination": {}})
        )
        result = await apollo_client._request(
            "POST", "/v1/mixed_people/search", json={},
        )
        assert result == {"people": [], "pagination": {}}

    @pytest.mark.asyncio
    @respx.mock
    async def test_successful_get(self, apollo_client):
        respx.get("https://api.apollo.io/v1/organizations/enrich").mock(
            return_value=httpx.Response(200, json={"organization": {}})
        )
        result = await apollo_client._request(
            "GET", "/v1/organizations/enrich", params={"domain": "acme.com"},
        )
        assert result == {"organization": {}}

    @pytest.mark.asyncio
    @respx.mock
    async def test_4xx_raises_api_error(self, apollo_client):
        respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        with pytest.raises(ApolloAPIError) as exc_info:
            await apollo_client._request(
                "POST", "/v1/mixed_people/search", json={},
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_key_header(self, apollo_client):
        route = respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(200, json={})
        )
        await apollo_client._request("POST", "/v1/mixed_people/search", json={})
        assert route.calls[0].request.headers["x-api-key"] == "test-apollo-key"

    @pytest.mark.asyncio
    @respx.mock
    async def test_close(self, apollo_client):
        await apollo_client.close()
        # Verify client is closed (subsequent calls would fail)
        assert apollo_client._client.is_closed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestApolloClientRequest -v`
Expected: FAIL (ApolloClient class doesn't exist yet)

- [ ] **Step 3: Write the ApolloClient class with _request and close**

Append to `tools/apollo_client.py`:

```python
# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ApolloClient:
    """Async client for Apollo.io API."""

    def __init__(self, api_key: str, base_url: str = "https://api.apollo.io"):
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
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
        """Send an HTTP request with retry on 429/5xx.

        Raises ApolloAPIError on non-retryable 4xx errors.
        """
        response = await self._client.request(
            method, path, json=json, params=params,
        )

        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()  # triggers tenacity retry

        if 400 <= response.status_code < 500:
            raise ApolloAPIError(
                status_code=response.status_code,
                detail=response.text,
            )

        logger.info(
            "apollo_api_call",
            extra={"method": method, "path": path, "status": response.status_code},
        )
        return response.json() if response.content else {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestApolloClientRequest -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add tools/apollo_client.py tests/test_apollo_client.py
git commit -m "feat(apollo): add ApolloClient with _request and retry logic"
```

---

### Task 3: Fixture Data

**Files:**
- Create: `tests/fixtures/apollo_sample_responses.json`

- [ ] **Step 1: Create fixture file**

```json
{
  "people_search": {
    "people": [
      {
        "id": "apl_001",
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane@acme.com",
        "title": "VP Engineering",
        "organization": {
          "name": "Acme Corp",
          "primary_domain": "acme.com"
        },
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "phone_numbers": [{"sanitized_number": "+1234567890"}]
      },
      {
        "id": "apl_002",
        "first_name": "John",
        "last_name": "Doe",
        "email": "john@beta.io",
        "title": "CTO",
        "organization": {
          "name": "Beta Inc",
          "primary_domain": "beta.io"
        },
        "linkedin_url": "https://linkedin.com/in/johndoe",
        "phone_numbers": []
      }
    ],
    "pagination": {
      "total_entries": 150,
      "page": 1,
      "per_page": 25
    }
  },
  "people_search_empty": {
    "people": [],
    "pagination": {
      "total_entries": 0,
      "page": 1,
      "per_page": 25
    }
  },
  "org_search": {
    "organizations": [
      {
        "id": "org_001",
        "name": "Acme Corp",
        "primary_domain": "acme.com",
        "industry": "Software",
        "estimated_num_employees": 500,
        "current_technologies": [{"name": "React"}, {"name": "PostgreSQL"}],
        "funding_stage": "Series B",
        "total_funding": 45000000,
        "short_description": "Developer tools company",
        "linkedin_url": "https://linkedin.com/company/acme"
      }
    ],
    "pagination": {
      "total_entries": 30,
      "page": 1,
      "per_page": 25
    }
  },
  "person_enrichment": {
    "person": {
      "id": "apl_003",
      "first_name": "Alice",
      "last_name": "Chen",
      "email": "alice@gamma.dev",
      "title": "Staff Engineer",
      "organization": {
        "name": "Gamma Dev",
        "primary_domain": "gamma.dev"
      },
      "linkedin_url": "https://linkedin.com/in/alicechen",
      "phone_numbers": [{"sanitized_number": "+9876543210"}]
    }
  },
  "person_enrichment_not_found": {
    "person": null
  },
  "org_enrichment": {
    "organization": {
      "id": "org_002",
      "name": "Delta Systems",
      "primary_domain": "delta.systems",
      "industry": "Cloud Infrastructure",
      "estimated_num_employees": 1200,
      "current_technologies": [{"name": "Kubernetes"}, {"name": "Go"}, {"name": "Terraform"}],
      "funding_stage": "Series C",
      "total_funding": 120000000,
      "short_description": "Cloud infrastructure provider",
      "linkedin_url": "https://linkedin.com/company/delta-systems"
    }
  },
  "org_enrichment_not_found": {
    "organization": null
  }
}
```

- [ ] **Step 2: Commit**

```bash
cd .
git add tests/fixtures/apollo_sample_responses.json
git commit -m "test(apollo): add fixture data for Apollo API responses"
```

---

### Task 4: People Search and Organization Search Methods

**Files:**
- Modify: `tools/apollo_client.py`
- Modify: `tests/test_apollo_client.py`

- [ ] **Step 1: Write failing tests for search methods**

Add to `tests/test_apollo_client.py`:

```python
import json
from pathlib import Path


@pytest.fixture
def apollo_fixtures():
    path = Path(__file__).parent / "fixtures" / "apollo_sample_responses.json"
    return json.loads(path.read_text())


class TestSearchPeople:
    """Test people search endpoint."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_basic_search(self, apollo_client, apollo_fixtures):
        respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        result = await apollo_client.search_people(title="VP Engineering")
        assert isinstance(result, PeopleSearchResult)
        assert len(result.contacts) == 2
        assert result.contacts[0].first_name == "Jane"
        assert result.contacts[0].email == "jane@acme.com"
        assert result.contacts[0].company_name == "Acme Corp"
        assert result.contacts[0].company_domain == "acme.com"
        assert result.total == 150

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_with_filters(self, apollo_client, apollo_fixtures):
        route = respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        await apollo_client.search_people(
            title="CTO", industry="Software",
            min_headcount=100, max_headcount=1000, limit=10,
        )
        payload = json.loads(route.calls[0].request.content)
        assert payload["person_titles"] == ["CTO"]
        assert payload["organization_industry_tag_ids"] == ["Software"]
        assert payload["organization_num_employees_ranges"] == ["100,1000"]
        assert payload["per_page"] == 10

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_empty_results(self, apollo_client, apollo_fixtures):
        respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(
                200, json=apollo_fixtures["people_search_empty"],
            )
        )
        result = await apollo_client.search_people(title="Nonexistent Role")
        assert len(result.contacts) == 0
        assert result.total == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_phone_extraction(self, apollo_client, apollo_fixtures):
        respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        result = await apollo_client.search_people()
        assert result.contacts[0].phone == "+1234567890"
        assert result.contacts[1].phone is None  # empty phone_numbers


class TestSearchOrganizations:
    """Test organization search endpoint."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_basic_search(self, apollo_client, apollo_fixtures):
        respx.post("https://api.apollo.io/v1/mixed_companies/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_search"])
        )
        result = await apollo_client.search_organizations(industry="Software")
        assert isinstance(result, OrgSearchResult)
        assert len(result.organizations) == 1
        assert result.organizations[0].name == "Acme Corp"
        assert result.organizations[0].tech_stack == ["React", "PostgreSQL"]
        assert result.organizations[0].estimated_headcount == 500
        assert result.total == 30

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_with_tech_stack(self, apollo_client, apollo_fixtures):
        route = respx.post("https://api.apollo.io/v1/mixed_companies/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_search"])
        )
        await apollo_client.search_organizations(
            tech_stack=["React", "PostgreSQL"], min_headcount=50,
        )
        payload = json.loads(route.calls[0].request.content)
        assert payload["currently_using_any_of_technology_uids"] == [
            "React", "PostgreSQL",
        ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestSearchPeople tests/test_apollo_client.py::TestSearchOrganizations -v`
Expected: FAIL (methods don't exist)

- [ ] **Step 3: Implement search methods**

Add to `ApolloClient` class in `tools/apollo_client.py`:

```python
    # -- People Search ----------------------------------------------------

    async def search_people(
        self,
        *,
        title: str | None = None,
        company: str | None = None,
        industry: str | None = None,
        min_headcount: int | None = None,
        max_headcount: int | None = None,
        limit: int = 25,
    ) -> PeopleSearchResult:
        """POST /v1/mixed_people/search — find contacts by criteria.

        No auto-pagination. Max limit per Apollo docs: 100.
        """
        payload: dict[str, Any] = {"per_page": limit, "page": 1}

        if title:
            payload["person_titles"] = [title]
        if company:
            payload["q_organization_name"] = company
        if industry:
            payload["organization_industry_tag_ids"] = [industry]
        if min_headcount is not None or max_headcount is not None:
            lo = min_headcount or 1
            hi = max_headcount or 1_000_000
            payload["organization_num_employees_ranges"] = [f"{lo},{hi}"]

        data = await self._request("POST", "/v1/mixed_people/search", json=payload)

        contacts = []
        for person in data.get("people", []):
            org = person.get("organization", {}) or {}
            phones = person.get("phone_numbers", [])
            contacts.append(ApolloContact(
                id=person["id"],
                first_name=person.get("first_name", ""),
                last_name=person.get("last_name", ""),
                email=person.get("email"),
                title=person.get("title"),
                company_name=org.get("name"),
                company_domain=org.get("primary_domain"),
                linkedin_url=person.get("linkedin_url"),
                phone=phones[0]["sanitized_number"] if phones else None,
            ))

        pagination = data.get("pagination", {})
        return PeopleSearchResult(
            contacts=contacts,
            total=pagination.get("total_entries", len(contacts)),
            page=pagination.get("page", 1),
            per_page=pagination.get("per_page", limit),
        )

    # -- Organization Search ----------------------------------------------

    async def search_organizations(
        self,
        *,
        industry: str | None = None,
        min_headcount: int | None = None,
        max_headcount: int | None = None,
        tech_stack: list[str] | None = None,
        limit: int = 25,
    ) -> OrgSearchResult:
        """POST /v1/mixed_companies/search — find orgs by criteria."""
        payload: dict[str, Any] = {"per_page": limit, "page": 1}

        if industry:
            payload["organization_industry_tag_ids"] = [industry]
        if min_headcount is not None or max_headcount is not None:
            lo = min_headcount or 1
            hi = max_headcount or 1_000_000
            payload["organization_num_employees_ranges"] = [f"{lo},{hi}"]
        if tech_stack:
            payload["currently_using_any_of_technology_uids"] = tech_stack

        data = await self._request(
            "POST", "/v1/mixed_companies/search", json=payload,
        )

        organizations = []
        for org in data.get("organizations", []):
            techs = [
                t["name"] for t in org.get("current_technologies", [])
                if isinstance(t, dict) and "name" in t
            ]
            organizations.append(ApolloOrganization(
                id=org["id"],
                name=org.get("name", ""),
                domain=org.get("primary_domain"),
                industry=org.get("industry"),
                estimated_headcount=org.get("estimated_num_employees"),
                tech_stack=techs,
                funding_stage=org.get("funding_stage"),
                funding_total=org.get("total_funding"),
                description=org.get("short_description"),
                linkedin_url=org.get("linkedin_url"),
            ))

        pagination = data.get("pagination", {})
        return OrgSearchResult(
            organizations=organizations,
            total=pagination.get("total_entries", len(organizations)),
            page=pagination.get("page", 1),
            per_page=pagination.get("per_page", limit),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestSearchPeople tests/test_apollo_client.py::TestSearchOrganizations -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd .
git add tools/apollo_client.py tests/test_apollo_client.py
git commit -m "feat(apollo): add people and organization search methods"
```

---

### Task 5: Person and Organization Enrichment Methods

**Files:**
- Modify: `tools/apollo_client.py`
- Modify: `tests/test_apollo_client.py`

- [ ] **Step 1: Write failing tests for enrichment**

Add to `tests/test_apollo_client.py`:

```python
class TestEnrichPerson:
    """Test person enrichment endpoint."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_enrich_by_email(self, apollo_client, apollo_fixtures):
        route = respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(
                200, json=apollo_fixtures["person_enrichment"],
            )
        )
        contact = await apollo_client.enrich_person(email="alice@gamma.dev")
        assert contact is not None
        assert contact.first_name == "Alice"
        assert contact.email == "alice@gamma.dev"
        assert contact.phone == "+9876543210"
        payload = json.loads(route.calls[0].request.content)
        assert payload["email"] == "alice@gamma.dev"

    @pytest.mark.asyncio
    @respx.mock
    async def test_enrich_by_linkedin(self, apollo_client, apollo_fixtures):
        route = respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(
                200, json=apollo_fixtures["person_enrichment"],
            )
        )
        await apollo_client.enrich_person(
            linkedin_url="https://linkedin.com/in/alicechen",
        )
        payload = json.loads(route.calls[0].request.content)
        assert payload["linkedin_url"] == "https://linkedin.com/in/alicechen"

    @pytest.mark.asyncio
    @respx.mock
    async def test_enrich_not_found(self, apollo_client, apollo_fixtures):
        respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(
                200, json=apollo_fixtures["person_enrichment_not_found"],
            )
        )
        result = await apollo_client.enrich_person(email="nobody@nowhere.com")
        assert result is None


class TestEnrichOrganization:
    """Test organization enrichment endpoint."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_enrich_by_domain(self, apollo_client, apollo_fixtures):
        respx.get("https://api.apollo.io/v1/organizations/enrich").mock(
            return_value=httpx.Response(
                200, json=apollo_fixtures["org_enrichment"],
            )
        )
        org = await apollo_client.enrich_organization("delta.systems")
        assert org is not None
        assert org.name == "Delta Systems"
        assert org.estimated_headcount == 1200
        assert org.tech_stack == ["Kubernetes", "Go", "Terraform"]
        assert org.funding_stage == "Series C"

    @pytest.mark.asyncio
    @respx.mock
    async def test_enrich_not_found(self, apollo_client, apollo_fixtures):
        respx.get("https://api.apollo.io/v1/organizations/enrich").mock(
            return_value=httpx.Response(
                200, json=apollo_fixtures["org_enrichment_not_found"],
            )
        )
        result = await apollo_client.enrich_organization("nonexistent.xyz")
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestEnrichPerson tests/test_apollo_client.py::TestEnrichOrganization -v`
Expected: FAIL (methods don't exist)

- [ ] **Step 3: Implement enrichment methods**

Add to `ApolloClient` class in `tools/apollo_client.py`:

```python
    # -- Person Enrichment ------------------------------------------------

    async def enrich_person(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
    ) -> ApolloContact | None:
        """POST /v1/people/match — enrich person by email or LinkedIn URL.

        Returns None if person not found.
        """
        payload: dict[str, Any] = {}
        if email:
            payload["email"] = email
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url

        data = await self._request("POST", "/v1/people/match", json=payload)

        person = data.get("person")
        if not person:
            return None

        org = person.get("organization", {}) or {}
        phones = person.get("phone_numbers", [])
        return ApolloContact(
            id=person["id"],
            first_name=person.get("first_name", ""),
            last_name=person.get("last_name", ""),
            email=person.get("email"),
            title=person.get("title"),
            company_name=org.get("name"),
            company_domain=org.get("primary_domain"),
            linkedin_url=person.get("linkedin_url"),
            phone=phones[0]["sanitized_number"] if phones else None,
        )

    # -- Organization Enrichment ------------------------------------------

    async def enrich_organization(self, domain: str) -> ApolloOrganization | None:
        """GET /v1/organizations/enrich — enrich org by domain.

        Returns None if org not found.
        """
        data = await self._request(
            "GET", "/v1/organizations/enrich", params={"domain": domain},
        )

        org = data.get("organization")
        if not org:
            return None

        techs = [
            t["name"] for t in org.get("current_technologies", [])
            if isinstance(t, dict) and "name" in t
        ]
        return ApolloOrganization(
            id=org["id"],
            name=org.get("name", ""),
            domain=org.get("primary_domain"),
            industry=org.get("industry"),
            estimated_headcount=org.get("estimated_num_employees"),
            tech_stack=techs,
            funding_stage=org.get("funding_stage"),
            funding_total=org.get("total_funding"),
            description=org.get("short_description"),
            linkedin_url=org.get("linkedin_url"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_apollo_client.py::TestEnrichPerson tests/test_apollo_client.py::TestEnrichOrganization -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run all client tests**

Run: `cd . && python -m pytest tests/test_apollo_client.py -v`
Expected: All ~28 tests PASS

- [ ] **Step 6: Commit**

```bash
cd .
git add tools/apollo_client.py tests/test_apollo_client.py
git commit -m "feat(apollo): add person and organization enrichment methods"
```

---

## Chunk 2: Agent Integration

### File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `agents/rex.py:117-124,381-414` | Add `apollo_client` param, `enrich_competitor_profile()`, enrichment in `execute()` |
| Modify | `agents/pax.py:92-104,130-150,430-491` | Add `apollo_client` param, new keywords, `prospect_leads()`, `enrich_and_upload()`, execute paths |
| Modify | `agents/atlas.py:130-145,187-205,550-620` | Wire `apollo_client` to Rex/Pax, main() creation + cleanup |
| Modify | `config/env.example` | Add `APOLLO_API_KEY` |
| Modify | `config/agent_config.yaml` | Add `apollo` under `api_clients` |
| Create | `tests/test_rex_apollo.py` | Rex enrichment tests |
| Create | `tests/test_pax_apollo.py` | Pax prospecting + upload tests |

---

### Task 6: Rex — Apollo Competitor Enrichment

**Files:**
- Modify: `agents/rex.py:117-124` (constructor), `agents/rex.py:381-414` (execute)
- Create: `tests/test_rex_apollo.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_rex_apollo.py
"""Tests for Rex Apollo.io integration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.rex import Rex
from tools.apollo_client import ApolloOrganization


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
    """Test competitor enrichment via Apollo."""

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
        mock_apollo.enrich_organization.assert_called_once_with("competitor.io")

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


class TestRexExecuteWithApollo:
    """Test that execute() enriches competitors when Apollo is available."""

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
    async def test_execute_without_apollo_has_no_enriched(
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
        assert result.get("enriched_profiles", []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_rex_apollo.py -v`
Expected: FAIL (Rex doesn't accept `apollo_client`)

- [ ] **Step 3: Modify Rex constructor and add enrichment method**

In `agents/rex.py`, update the constructor (around line 117-132):

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    search_tools: Optional[SearchTools] = None,
    apollo_client: Optional["ApolloClient"] = None,
    product_name: str = "the target product",
):
    self.api_client = api_client
    self.knowledge_base_path = knowledge_base_path
    self.llm_client = llm_client
    self.search_tools = search_tools
    self.apollo_client = apollo_client
    self.product_name = product_name
    self._kb = KnowledgeBaseSearch(
        knowledge_base_path, extra_stop_words=REX_STOP_WORDS,
    )
```

Add the enrichment method (after `_extract_upstream_context`, before `execute`):

```python
async def enrich_competitor_profile(
    self, name: str, domain: str,
) -> dict[str, Any] | None:
    """Enrich a competitor with Apollo org data.

    Returns dict with firmographic data, or None if unavailable.
    """
    if not self.apollo_client:
        return None
    try:
        org = await self.apollo_client.enrich_organization(domain)
    except Exception as exc:
        logger.warning(f"Apollo enrichment failed for {domain}: {exc}")
        return None
    if not org:
        return None
    return {
        "name": name,
        "domain": domain,
        "tech_stack": org.tech_stack,
        "estimated_headcount": org.estimated_headcount,
        "funding_stage": org.funding_stage,
        "funding_total": org.funding_total,
        "industry": org.industry,
    }
```

In Rex's `execute()` method, after the web search section (after line ~287) and before building the prompt (line ~298), add Apollo enrichment.
**Important:** Initialize `enriched_profiles` outside the `if` block so it's always defined:

```python
        # 2b. Apollo enrichment per competitor (if available)
        enriched_profiles: list[dict[str, Any]] = []
        if self.apollo_client:
            for comp in competitors:
                # Use competitor name as domain heuristic (lowercase + .com)
                # TODO: Extract real domains from web_intel results or KB
                domain_guess = comp.lower().replace(" ", "") + ".com"
                profile = await self.enrich_competitor_profile(comp, domain_guess)
                if profile:
                    enriched_profiles.append(profile)
```

Before `return base_result` (around line 413), add:

```python
        base_result["enriched_profiles"] = enriched_profiles
```

Also add an `enriched_section` to the prompt if enriched profiles exist:

```python
        enriched_section = ""
        if enriched_profiles:
            enriched_section = "## Apollo Firmographic Data\n"
            for p in enriched_profiles:
                enriched_section += (
                    f"- {p['name']} ({p['domain']}): "
                    f"headcount={p.get('estimated_headcount', '?')}, "
                    f"funding={p.get('funding_stage', '?')}, "
                    f"tech={p.get('tech_stack', [])}\n"
                )
```

And insert `{enriched_section}` into the user_prompt string after `{issues_section}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_rex_apollo.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run existing Rex tests to check for regressions**

Run: `cd . && python -m pytest tests/test_rex.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd .
git add agents/rex.py tests/test_rex_apollo.py
git commit -m "feat(rex): add Apollo competitor enrichment"
```

---

### Task 7: Pax — Apollo Prospecting and Enrich-Upload

**Files:**
- Modify: `agents/pax.py:92-104` (ASSET_KEYWORDS), `agents/pax.py:130-150` (constructor), `agents/pax.py:430-491` (execute)
- Create: `tests/test_pax_apollo.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pax_apollo.py
"""Tests for Pax Apollo.io integration."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.pax import Pax
from tools.apollo_client import ApolloContact, PeopleSearchResult


@pytest.fixture
def mock_apollo():
    client = MagicMock()
    client.search_people = AsyncMock(return_value=PeopleSearchResult(
        contacts=[
            ApolloContact(
                id="apl_001", first_name="Jane", last_name="Smith",
                email="jane@acme.com", title="VP Engineering",
                company_name="Acme Corp", company_domain="acme.com",
                linkedin_url="https://linkedin.com/in/janesmith",
                phone="+1234567890",
            ),
            ApolloContact(
                id="apl_002", first_name="John", last_name="Doe",
                email="john@beta.io", title="CTO",
                company_name="Beta Inc", company_domain="beta.io",
            ),
        ],
        total=2, page=1, per_page=25,
    ))
    client.enrich_person = AsyncMock(return_value=None)  # no extra enrichment
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_instantly():
    client = MagicMock()
    client.add_leads_bulk = AsyncMock(return_value={"added": 2, "skipped": 0})
    client.close = AsyncMock()
    return client


@pytest.fixture
def pax_with_apollo(
    posthog_client, knowledge_base_path, mock_llm_client,
    mock_apollo, mock_instantly,
):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        instantly_client=mock_instantly,
        apollo_client=mock_apollo,
    )


@pytest.fixture
def pax_apollo_only(posthog_client, knowledge_base_path, mock_llm_client, mock_apollo):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        apollo_client=mock_apollo,
    )


@pytest.fixture
def pax_no_apollo(posthog_client, knowledge_base_path, mock_llm_client):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
    )


class TestProspectLeads:
    """Test Apollo people search via Pax."""

    @pytest.mark.asyncio
    async def test_basic_search(self, pax_with_apollo, mock_apollo):
        contacts = await pax_with_apollo.prospect_leads(
            {"title": "VP Engineering"},
        )
        assert len(contacts) == 2
        assert contacts[0].first_name == "Jane"
        mock_apollo.search_people.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_apollo_returns_empty(self, pax_no_apollo):
        contacts = await pax_no_apollo.prospect_leads({"title": "CTO"})
        assert contacts == []


class TestEnrichAndUpload:
    """Test the full enrich-then-upload pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline(
        self, pax_with_apollo, mock_apollo, mock_instantly,
    ):
        contacts = [
            ApolloContact(
                id="apl_001", first_name="Jane", last_name="Smith",
                email="jane@acme.com", title="VP Engineering",
                company_name="Acme Corp", company_domain="acme.com",
            ),
            ApolloContact(
                id="apl_002", first_name="John", last_name="Doe",
                email="john@beta.io", title="CTO",
                company_name="Beta Inc", company_domain="beta.io",
            ),
        ]
        result = await pax_with_apollo.enrich_and_upload(contacts, "camp_apollo_1")
        assert result["total_found"] == 2
        assert result["uploaded"] == 2
        mock_instantly.add_leads_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_contacts_without_email(
        self, pax_with_apollo, mock_instantly,
    ):
        contacts = [
            ApolloContact(id="a1", first_name="No", last_name="Email"),
            ApolloContact(
                id="a2", first_name="Has", last_name="Email",
                email="has@email.com",
            ),
        ]
        mock_instantly.add_leads_bulk = AsyncMock(
            return_value={"added": 1, "skipped": 0},
        )
        result = await pax_with_apollo.enrich_and_upload(contacts)
        assert result["skipped_no_email"] == 1
        assert result["uploaded"] == 1

    @pytest.mark.asyncio
    async def test_no_instantly_client(self, pax_apollo_only):
        contacts = [
            ApolloContact(
                id="a1", first_name="Jane", last_name="S",
                email="jane@co.com",
            ),
        ]
        result = await pax_apollo_only.enrich_and_upload(contacts)
        assert result["uploaded"] == 0
        assert result["total_found"] == 1

    @pytest.mark.asyncio
    async def test_batch_splitting(self, pax_with_apollo, mock_instantly):
        contacts = [
            ApolloContact(
                id=f"a{i}", first_name=f"User{i}", last_name="Test",
                email=f"user{i}@co.com",
            )
            for i in range(1500)
        ]
        mock_instantly.add_leads_bulk = AsyncMock(
            side_effect=[
                {"added": 1000, "skipped": 0},
                {"added": 500, "skipped": 0},
            ],
        )
        pax_with_apollo.BULK_BATCH_SIZE = 1000
        result = await pax_with_apollo.enrich_and_upload(contacts)
        assert result["uploaded"] == 1500
        assert mock_instantly.add_leads_bulk.call_count == 2


class TestPaxExecuteApollo:
    """Test execute() with Apollo asset types."""

    @pytest.mark.asyncio
    async def test_prospect_leads_execute(
        self, pax_with_apollo, mock_llm_client, mock_apollo, mock_instantly,
    ):
        mock_llm_client.generate = AsyncMock(return_value=json.dumps({
            "title": "VP Engineering",
            "industry": "Software",
        }))
        result = await pax_with_apollo.execute(
            "Prospect and find leads matching our ICP",
        )
        assert result["agent"] == "pax"
        assert result["asset_type"] == "prospect_leads"

    @pytest.mark.asyncio
    async def test_enrich_upload_execute(
        self, pax_with_apollo, mock_llm_client, mock_apollo, mock_instantly,
    ):
        result = await pax_with_apollo.execute(
            "Enrich and upload contacts to campaign",
            context={"apollo_contacts": [
                {"id": "a1", "first_name": "J", "last_name": "S", "email": "j@co.com"},
            ]},
        )
        assert result["agent"] == "pax"
        assert result["asset_type"] == "enrich_upload"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_pax_apollo.py -v`
Expected: FAIL (Pax doesn't accept `apollo_client`)

- [ ] **Step 3: Modify Pax**

In `agents/pax.py`, update `ASSET_KEYWORDS` (around line 92-104). Insert `prospect_leads` and `enrich_upload` **before** `instantly_campaign`:

```python
    ASSET_KEYWORDS: dict[str, list[str]] = {
        "triage_replies": ["triage", "replies", "follow-up"],
        "lead_upload": ["upload leads", "import leads", "add leads"],
        "prospect_leads": ["find leads", "apollo search", "icp", "prospect leads"],
        "enrich_upload": ["enrich", "enrich and upload", "apollo enrich"],
        "instantly_campaign": ["instantly", "cold email", "outreach campaign"],
        "nurture": ["nurture", "drip", "sequence"],
        "battle_card": ["battle card", "vs", "comparison"],
        "outreach": ["outreach", "email", "prospect"],
        "one_pager": ["one-pager", "one pager", "summary"],
        "objection": ["objection", "faq", "pushback"],
    }
```

Update the constructor (around line 130-150):

```python
    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        instantly_client: Optional[InstantlyClient] = None,
        apollo_client: Optional["ApolloClient"] = None,
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.instantly_client = instantly_client
        self.apollo_client = apollo_client
        self.product_name = product_name
        self.BULK_BATCH_SIZE = 1000
        self._kb = KnowledgeBaseSearch(
            knowledge_base_path,
            extra_stop_words=frozenset({
                "generate", "create", "write", "outreach", "emails", "battle",
                "card", "nurture", "sequence", "one-pager",
            }),
        )
```

Add new methods after `upload_leads` (around line 220):

```python
    async def prospect_leads(
        self, criteria: dict,
    ) -> list["ApolloContact"]:
        """Search Apollo for contacts matching ICP criteria.

        criteria keys: title, company, industry, min_headcount, max_headcount
        Returns list of ApolloContact. Returns [] if no apollo_client.
        """
        if not self.apollo_client:
            return []
        result = await self.apollo_client.search_people(**criteria)
        return result.contacts

    async def enrich_and_upload(
        self,
        contacts: list["ApolloContact"],
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        """Enrich Apollo contacts and upload to Instantly.

        Converts ApolloContact -> InstantlyLead, filters out those
        without email, uploads in batches.
        Args:
            contacts: Apollo contacts to convert and upload.
            campaign_id: Instantly campaign ID to upload leads to.
        """
        total_found = len(contacts)
        leads = []
        skipped = 0
        for contact in contacts:
            lead = contact.to_instantly_lead()
            if lead.email:
                leads.append(lead)
            else:
                skipped += 1

        uploaded = 0
        errors: list[str] = []
        if leads and self.instantly_client:
            for i in range(0, len(leads), self.BULK_BATCH_SIZE):
                batch = leads[i : i + self.BULK_BATCH_SIZE]
                try:
                    result = await self.instantly_client.add_leads_bulk(
                        campaign_id or "", batch,
                    )
                    uploaded += result.get("added", len(batch))
                except Exception as e:
                    errors.append(str(e))
                    logger.warning(f"Apollo lead upload batch failed: {e}")

        return {
            "total_found": total_found,
            "uploaded": uploaded,
            "skipped_no_email": skipped,
            "errors": errors,
        }
```

In `execute()` (around line 455), add new asset type handlers **before** the `instantly_campaign` check:

```python
        # Handle Apollo-specific asset types
        if asset_type == "prospect_leads" and self.apollo_client:
            criteria = {}
            if self.llm_client:
                try:
                    from agents.base import strip_markdown_fences
                    raw = await self.llm_client.generate(
                        system_prompt="Extract ICP criteria from the task.",
                        user_prompt=(
                            f"Extract search criteria from: {task}\n"
                            'Return JSON: {"title": "...", "industry": "..."}'
                        ),
                        temperature=0.0,
                    )
                    criteria = json.loads(strip_markdown_fences(raw))
                except Exception:
                    pass
            contacts = await self.prospect_leads(criteria)
            upload_result = {}
            if contacts and self.instantly_client:
                upload_result = await self.enrich_and_upload(contacts)
            return {
                "agent": "pax", "task": task, "asset_type": asset_type,
                "status": "prospected",
                "contacts_found": len(contacts),
                **upload_result,
            }

        if asset_type == "enrich_upload" and self.apollo_client:
            raw_contacts = (context or {}).get("apollo_contacts", [])
            from tools.apollo_client import ApolloContact as AC
            contacts = [
                AC(
                    id=c.get("id", ""),
                    first_name=c.get("first_name", ""),
                    last_name=c.get("last_name", ""),
                    email=c.get("email"),
                    title=c.get("title"),
                    company_name=c.get("company_name"),
                    company_domain=c.get("company_domain"),
                    linkedin_url=c.get("linkedin_url"),
                    phone=c.get("phone"),
                )
                for c in raw_contacts
            ]
            result = await self.enrich_and_upload(contacts)
            return {
                "agent": "pax", "task": task, "asset_type": asset_type,
                "status": "enriched_and_uploaded", **result,
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_pax_apollo.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run existing Pax tests**

Run: `cd . && python -m pytest tests/test_pax.py tests/test_pax_instantly.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
cd .
git add agents/pax.py tests/test_pax_apollo.py
git commit -m "feat(pax): add Apollo prospecting and enrich-then-upload pipeline"
```

---

### Task 8: Atlas Wiring

**Files:**
- Modify: `agents/atlas.py:130-145` (constructor), `agents/atlas.py:187-205` (agent instantiation), `agents/atlas.py:550-620` (main)
- Modify: `config/env.example`
- Modify: `config/agent_config.yaml`

- [ ] **Step 1: Modify Atlas constructor**

In `agents/atlas.py`, update the `__init__` signature (around line 128-138).
Keep the existing parameter order and defaults — only add `apollo_client` after `instantly_client`:

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
        apollo_client: Optional["ApolloClient"] = None,
    ):
```

Add `self.apollo_client = apollo_client` after `self.instantly_client = instantly_client`.

Update Rex instantiation (around line 187):

```python
        self.rex = Rex(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            search_tools=search_tools,
            apollo_client=apollo_client,
        )
```

Update Pax instantiation (around line 193):

```python
        self.pax = Pax(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            instantly_client=instantly_client,
            apollo_client=apollo_client,
        )
```

- [ ] **Step 2: Update main() for Apollo client creation and cleanup**

In `main()` (around line 556), after the `instantly_client` creation block:

```python
    apollo_client = None
    apollo_key = os.environ.get("APOLLO_API_KEY")
    if apollo_key:
        from tools.apollo_client import ApolloClient
        apollo_client = ApolloClient(api_key=apollo_key)
```

Update the Atlas instantiation (around line 558):

```python
    atlas = Atlas(
        api_client=client,
        knowledge_base_path=kb_path,
        llm_client=llm_client,
        github_tools=github_tools,
        search_tools=search,
        config=config,
        instantly_client=instantly_client,
        apollo_client=apollo_client,
    )
```

In the `finally` block (around line 616), add cleanup:

```python
        if apollo_client:
            await apollo_client.close()
```

- [ ] **Step 3: Update config files**

In `config/env.example`, add after the Instantly section:

```
# Apollo.io Configuration
APOLLO_API_KEY=your_apollo_api_key_here
```

In `config/agent_config.yaml`, add under `api_clients:` (after the `instantly:` section):

```yaml
  apollo:
    base_url: https://api.apollo.io
    rate_limit_rpm: 50
```

- [ ] **Step 4: Run existing Atlas tests**

Run: `cd . && python -m pytest tests/test_atlas.py tests/test_atlas_replies.py -v`
Expected: All existing tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd . && python -m pytest tests/ -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 6: Lint check**

Run: `cd . && python -m ruff check agents/atlas.py agents/rex.py agents/pax.py tools/apollo_client.py`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
cd .
git add agents/atlas.py config/env.example config/agent_config.yaml
git commit -m "feat(atlas): wire Apollo client to Rex and Pax agents"
```

---

### Task 9: Final Verification

- [ ] **Step 1: Full test suite**

Run: `cd . && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS, including new Apollo tests

- [ ] **Step 2: Lint entire project**

Run: `cd . && python -m ruff check .`
Expected: No errors

- [ ] **Step 3: Verify test count increased**

Run: `cd . && python -m pytest tests/ -v --co -q | tail -1`
Expected: Count is ~35-40 higher than before (new Apollo tests)
