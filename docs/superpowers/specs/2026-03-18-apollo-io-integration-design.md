# Apollo.io Integration Design Spec

## Summary

Integrate Apollo.io's Prospecting and Enrichment APIs into the multi-agent system. Apollo serves as the **data layer** that feeds enriched leads into the existing Instantly outreach pipeline (Pax) and enriches competitor profiles with firmographic/technographic data (Rex).

## Scope

**In scope:**
- New `tools/apollo_client.py` async API client (4 endpoints)
- Pax integration: prospect leads + enrich-then-upload to Instantly
- Rex integration: enrich competitor profiles with org data
- Atlas wiring: pass client, extend SharedContext
- Config + env updates
- Full test coverage with respx mocks

**Out of scope:**
- Apollo Sequences (overlaps with Instantly)
- Apollo CRM/deal tracking
- Intent data / website visitor tracking
- Apollo webhooks

## Architecture

### Pattern

Mirrors the Instantly integration exactly: thin async API client in `tools/`, injected into agents via constructor, wired by Atlas.

```
Apollo API
    ↑
tools/apollo_client.py  (async httpx + tenacity retry)
    ↑
    ├── agents/pax.py    (prospect_leads, enrich_and_upload)
    └── agents/rex.py    (enrich_competitor_profile)
    ↑
agents/atlas.py          (creates client, passes to agents, cleanup)
```

### Data Flow

```
Rex discovers competitors (existing flow)
    → Apollo org enrichment (new)
    → Enriched profiles stored in SharedContext.rex_competitive["enriched_profiles"]
    → Pax reads enriched profiles to refine ICP targeting

Pax receives sales task (existing flow)
    → Apollo people search (new) — finds contacts matching ICP
    → Apollo person enrichment (new) — adds firmographic detail
    → Convert ApolloContact → InstantlyLead
    → Upload to Instantly in batches of 1000 (existing flow)
```

## Component Design

### 1. Apollo API Client (`tools/apollo_client.py`)

#### API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/v1/mixed_people/search` | Search for contacts by title, company, industry |
| `POST` | `/v1/mixed_companies/search` | Search for organizations by industry, headcount, tech stack |
| `POST` | `/v1/people/match` | Enrich a person by email or LinkedIn URL |
| `GET` | `/v1/organizations/enrich` | Enrich an organization by domain |

#### DTOs (dataclasses)

```python
@dataclass
class ApolloContact:
    id: str
    first_name: str
    last_name: str
    email: str | None
    title: str | None
    company_name: str | None
    company_domain: str | None
    linkedin_url: str | None
    phone: str | None

    def to_instantly_lead(self) -> "InstantlyLead":
        """Convert to InstantlyLead for Instantly upload.

        Field mapping:
        - email → email (required, skip if None)
        - first_name → first_name
        - last_name → last_name
        - company_name → company_name
        - phone, linkedin_url, title → custom_variables
        """
        from tools.instantly_client import InstantlyLead
        return InstantlyLead(
            email=self.email or "",
            first_name=self.first_name,
            last_name=self.last_name,
            company_name=self.company_name or "",
            custom_variables={
                k: v for k, v in {
                    "phone": self.phone,
                    "linkedin_url": self.linkedin_url,
                    "title": self.title,
                }.items() if v
            },
        )

@dataclass
class ApolloOrganization:
    id: str
    name: str
    domain: str | None
    industry: str | None
    estimated_headcount: int | None
    tech_stack: list[str]
    funding_stage: str | None
    funding_total: float | None
    description: str | None
    linkedin_url: str | None

@dataclass
class PeopleSearchResult:
    contacts: list[ApolloContact]
    total: int
    page: int
    per_page: int

@dataclass
class OrgSearchResult:
    organizations: list[ApolloOrganization]
    total: int
    page: int
    per_page: int
```

#### Client class

```python
class ApolloAPIError(Exception):
    """Raised for non-retryable Apollo API errors (4xx except 429)."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Apollo API error {status_code}: {detail}")

class ApolloClient:
    def __init__(self, api_key: str, base_url: str = "https://api.apollo.io"):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Send request with retry on 429/5xx. Raises ApolloAPIError on 4xx."""
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()  # triggers tenacity retry
        if resp.status_code >= 400:
            raise ApolloAPIError(resp.status_code, resp.text)
        return resp.json()

    async def search_people(self, *, title: str | None = None,
                            company: str | None = None,
                            industry: str | None = None,
                            min_headcount: int | None = None,
                            max_headcount: int | None = None,
                            limit: int = 25) -> PeopleSearchResult:
        """POST /v1/mixed_people/search — find contacts by criteria.

        No auto-pagination. Caller controls page via limit param.
        Max limit per Apollo docs: 100.
        """
        ...

    async def search_organizations(self, *, industry: str | None = None,
                                    min_headcount: int | None = None,
                                    max_headcount: int | None = None,
                                    tech_stack: list[str] | None = None,
                                    limit: int = 25) -> OrgSearchResult:
        """POST /v1/mixed_companies/search — find orgs by criteria."""
        ...

    async def enrich_person(self, *, email: str | None = None,
                            linkedin_url: str | None = None) -> ApolloContact | None:
        """POST /v1/people/match — enrich person by email or LinkedIn URL.

        Returns None if person not found (Apollo returns empty match).
        Raises ApolloAPIError on auth/server errors.
        """
        ...

    async def enrich_organization(self, domain: str) -> ApolloOrganization | None:
        """GET /v1/organizations/enrich?domain=... — enrich org by domain.

        Returns None if org not found.
        Raises ApolloAPIError on auth/server errors.
        """
        ...

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
```

**Auth:** `x-api-key` header (cleaner than query param — no key in URLs/logs).

**Retry:** tenacity — retry on 429 and 5xx with exponential backoff, matching the Instantly pattern exactly.

**Rate limiting:** Handled entirely via tenacity retry on 429 (same as Instantly). No shared rate limiter needed.

**Pagination:** No auto-pagination. Methods accept `limit` (max 100 per Apollo docs). Caller responsible for multiple calls if needed.

### 2. Pax Integration (`agents/pax.py`)

#### Updated constructor

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    instantly_client: Optional[InstantlyClient] = None,
    apollo_client: Optional[ApolloClient] = None,  # NEW
    product_name: str = "the target product",
):
    # ... existing init ...
    self.apollo_client = apollo_client  # NEW
```

#### New methods

```python
async def prospect_leads(self, criteria: dict) -> list[ApolloContact]:
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
    contacts: list[ApolloContact],
    campaign_name: str | None = None,
) -> dict[str, Any]:
    """Enrich Apollo contacts and upload to Instantly.

    Steps:
    1. For contacts missing email, attempt Apollo person enrichment
    2. Filter out contacts with no email (can't upload to Instantly)
    3. Convert ApolloContact → InstantlyLead via to_instantly_lead()
    4. Upload to Instantly in batches of BULK_BATCH_SIZE
    Returns: {"total_found": N, "enriched": N, "uploaded": N, "skipped_no_email": N}
    """
    ...
```

#### New ASSET_KEYWORDS entries

Insert **before** `"instantly_campaign"` (to prevent "prospect" matching "outreach"):

```python
ASSET_KEYWORDS: dict[str, list[str]] = {
    "triage_replies": ["triage", "replies", "follow-up"],
    "lead_upload": ["upload leads", "import leads", "add leads"],
    "prospect_leads": ["prospect", "find leads", "apollo search", "icp"],  # NEW — before instantly_campaign
    "enrich_upload": ["enrich", "enrich and upload", "apollo enrich"],     # NEW — before instantly_campaign
    "instantly_campaign": ["instantly", "cold email", "outreach campaign"],
    "nurture": ["nurture", "drip", "sequence"],
    "battle_card": ["battle card", "vs", "comparison"],
    "outreach": ["outreach", "email", "prospect"],
    "one_pager": ["one-pager", "one pager", "summary"],
    "objection": ["objection", "faq", "pushback"],
}
```

#### Execute paths

When task matches `prospect_leads`:
1. LLM parses ICP criteria from task string (JSON: `{"title": "...", "industry": "..."}`)
2. Call `prospect_leads(criteria)`
3. If `instantly_client` available, call `enrich_and_upload(contacts)`
4. Return `{"agent": "pax", "status": "prospected", "asset_type": "prospect_leads", ...}`

When task matches `enrich_upload`:
1. Parse contacts from context (`context.get("apollo_contacts", [])`)
2. Call `enrich_and_upload(contacts)`
3. Return upload stats

### 3. Rex Integration (`agents/rex.py`)

#### Updated constructor

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    search_tools: Optional[SearchTools] = None,
    apollo_client: Optional[ApolloClient] = None,  # NEW
    product_name: str = "the target product",
):
    # ... existing init ...
    self.apollo_client = apollo_client  # NEW
```

#### New method

```python
async def enrich_competitor_profile(
    self, name: str, domain: str,
) -> dict[str, Any] | None:
    """Enrich a competitor with Apollo org data.

    Returns dict with: name, domain, tech_stack, estimated_headcount,
    funding_stage, funding_total, industry.
    Returns None if org not found or apollo_client unavailable.
    """
    if not self.apollo_client:
        return None
    org = await self.apollo_client.enrich_organization(domain)
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

#### Integration into execute()

After Rex's existing competitor discovery + web search flow, if `self.apollo_client` is available:
1. For each discovered competitor with a domain, call `enrich_competitor_profile(name, domain)`
2. Collect enriched profiles, skip None results
3. Add enriched data to the competitive analysis prompt (e.g., "Competitor X: 500 employees, Series B, uses React/Node.js")
4. Store enriched profiles in result dict: `base_result["enriched_profiles"] = enriched_profiles`

No new `ASSET_KEYWORDS` — enrichment is automatic within existing flow.

### 4. Atlas Wiring (`agents/atlas.py`)

#### SharedContext — NO new field

Apollo enrichment data flows through existing `rex_competitive` (Rex stores `enriched_profiles` in its result, which Atlas puts in `self.context.rex_competitive`). This follows the agent-based naming convention. No new SharedContext field needed.

#### Updated constructor

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    archive_dir: Path | None = None,
    llm_client: LLMClient | None = None,
    github_tools: GitHubTools | None = None,
    search_tools: SearchTools | None = None,
    instantly_client: InstantlyClient | None = None,
    apollo_client: ApolloClient | None = None,  # NEW
    config: AgentConfig | None = None,
):
    # ... existing init ...
    self.apollo_client = apollo_client  # NEW
```

#### Updated agent instantiation (lines ~187-205)

```python
self.rex = Rex(
    api_client=api_client,
    knowledge_base_path=knowledge_base_path,
    llm_client=llm_client,
    search_tools=search_tools,
    apollo_client=apollo_client,  # NEW
)
self.pax = Pax(
    api_client=api_client,
    knowledge_base_path=knowledge_base_path,
    llm_client=llm_client,
    instantly_client=instantly_client,
    apollo_client=apollo_client,  # NEW
)
# Mox does NOT get apollo_client — it has no Apollo use case
```

#### Updated main()

```python
# In main(), after instantly_client creation:
apollo_client = None
apollo_key = os.environ.get("APOLLO_API_KEY")
if apollo_key:
    from tools.apollo_client import ApolloClient
    apollo_client = ApolloClient(api_key=apollo_key)

# Pass to Atlas:
atlas = Atlas(
    ...,
    instantly_client=instantly_client,
    apollo_client=apollo_client,  # NEW
)

# In finally block, after instantly_client cleanup:
if apollo_client:
    await apollo_client.close()
```

#### No changes to `_compile_okrs()`

Apollo enrichment is passive (it enhances existing Rex/Pax results), not a separate OKR metric. No new OKR fields needed.

### 5. Config Changes

#### `config/env.example` — add after Instantly section

```
# Apollo.io Configuration
APOLLO_API_KEY=your_apollo_api_key_here
```

#### `config/agent_config.yaml` — add under `api_clients:`

```yaml
  apollo:
    base_url: https://api.apollo.io
    rate_limit_rpm: 50
```

## Testing Strategy

### `tests/test_apollo_client.py` (~12-15 tests)

- `TestApolloClientRequest`: retry on 429, retry on 5xx, raises ApolloAPIError on 4xx
- `TestSearchPeople`: basic search, with filters, empty results
- `TestSearchOrganizations`: basic search, with tech_stack filter
- `TestEnrichPerson`: by email, by linkedin_url, not found returns None
- `TestEnrichOrganization`: by domain, not found returns None
- `TestClientLifecycle`: close() cleans up httpx client

### `tests/test_pax_apollo.py` (~8-10 tests)

- `TestProspectLeads`: basic search returns contacts, no apollo_client returns []
- `TestEnrichAndUpload`: full pipeline (Apollo enrich -> Instantly upload), no instantly_client skips upload, contacts without email skipped, batch splitting at 1000
- `TestExecuteApollo`: execute with prospect_leads task, execute with enrich_upload task

### `tests/test_rex_apollo.py` (~5-7 tests)

- `TestEnrichCompetitor`: basic enrichment returns dict, domain not found returns None, no apollo_client returns None
- `TestExecuteWithApollo`: competitors enriched in execute flow, enriched_profiles in result

All tests use respx for HTTP mocking.

### Fixture data (`tests/fixtures/apollo_sample_responses.json`)

```json
{
  "people_search": {
    "people": [
      {
        "id": "apl_001",
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane@example.com",
        "title": "VP Engineering",
        "organization": {
          "name": "Acme Corp",
          "primary_domain": "acme.com"
        },
        "linkedin_url": "https://linkedin.com/in/janesmith",
        "phone_numbers": [{"sanitized_number": "+1234567890"}]
      }
    ],
    "pagination": {"total_entries": 150, "page": 1, "per_page": 25}
  },
  "org_enrichment": {
    "organization": {
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
  },
  "person_enrichment": {
    "person": {
      "id": "apl_002",
      "first_name": "John",
      "last_name": "Doe",
      "email": "john@example.com",
      "title": "CTO",
      "organization": {
        "name": "Beta Inc",
        "primary_domain": "beta.io"
      },
      "linkedin_url": "https://linkedin.com/in/johndoe",
      "phone_numbers": []
    }
  },
  "not_found": {
    "person": null,
    "organization": null
  }
}
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Apollo unavailable (network error) | tenacity retries 3x, then skip enrichment, log warning, proceed with bare data |
| Person/org not found | `enrich_person`/`enrich_organization` returns `None` |
| Rate limited (429) | tenacity retry with exponential backoff (2s, 4s, 8s... max 60s) |
| Invalid API key (401) | Raise `ApolloAPIError`, log error |
| Other 4xx | Raise `ApolloAPIError`, log error |

## Dependencies

No new Python packages needed. Apollo's REST API is called via httpx (already a dependency). Retry via tenacity (already a dependency).
