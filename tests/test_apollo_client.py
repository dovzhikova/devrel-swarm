# tests/test_apollo_client.py
"""Tests for Apollo.io API client."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from devrel_swarm.tools.apollo_client import (
    ApolloAPIError,
    ApolloClient,
    ApolloContact,
    ApolloOrganization,
    OrgSearchResult,
    PeopleSearchResult,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def apollo_client():
    return ApolloClient(api_key="test-apollo-key")


@pytest.fixture
def apollo_fixtures():
    path = Path(__file__).parent / "fixtures" / "apollo_sample_responses.json"
    return json.loads(path.read_text())


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


# ---------------------------------------------------------------------------
# Task 2: Client Core Tests
# ---------------------------------------------------------------------------


class TestApolloClientRequest:
    """Tests for ApolloClient._post(), auth header, and close()."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_post(self, apollo_client):
        """A 200 response returns parsed JSON."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(200, json={"people": [], "pagination": {"total_entries": 0, "page": 1, "per_page": 25}})
        )
        result = await apollo_client._post("/mixed_people/api_search", {"page": 1})
        assert "people" in result

    @respx.mock
    @pytest.mark.asyncio
    async def test_4xx_raises_apollo_api_error(self, apollo_client):
        """A 400 response raises ApolloAPIError (not retried)."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(400, json={"message": "Bad request"})
        )
        with pytest.raises(ApolloAPIError) as exc_info:
            await apollo_client._post("/mixed_people/api_search", {})
        assert exc_info.value.status_code == 400
        assert "Bad request" in exc_info.value.detail

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_raises_apollo_api_error(self, apollo_client):
        """A 401 Unauthorized response raises ApolloAPIError."""
        respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(401, json={"message": "Invalid API key"})
        )
        with pytest.raises(ApolloAPIError) as exc_info:
            await apollo_client._post("/people/match", {})
        assert exc_info.value.status_code == 401

    @respx.mock
    @pytest.mark.asyncio
    async def test_x_api_key_header_sent(self, apollo_client):
        """The x-api-key header is included in every request."""
        sent_headers = {}

        def capture_request(request):
            sent_headers.update(dict(request.headers))
            return httpx.Response(200, json={"people": [], "pagination": {"total_entries": 0, "page": 1, "per_page": 25}})

        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(side_effect=capture_request)
        await apollo_client._post("/mixed_people/api_search", {})
        assert sent_headers.get("x-api-key") == "test-apollo-key"

    @pytest.mark.asyncio
    async def test_close_does_not_raise(self, apollo_client):
        """close() can be called without errors."""
        await apollo_client.close()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Client works as async context manager."""
        async with ApolloClient(api_key="test-key") as client:
            assert client._api_key == "test-key"
        # After exiting, underlying client should be closed (no error raised)


# ---------------------------------------------------------------------------
# Task 4: People and Organization Search Tests
# ---------------------------------------------------------------------------


class TestSearchPeople:
    """Tests for ApolloClient.search_people()."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_basic_search_parses_contacts(self, apollo_client, apollo_fixtures):
        """Contacts are correctly parsed from API response."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        result = await apollo_client.search_people(titles=["VP Engineering"])

        assert isinstance(result, PeopleSearchResult)
        assert len(result.contacts) == 2
        assert result.total == 150
        assert result.page == 1
        assert result.per_page == 25

    @respx.mock
    @pytest.mark.asyncio
    async def test_contact_fields_parsed_correctly(self, apollo_client, apollo_fixtures):
        """First contact has correct field values including phone and domain."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        result = await apollo_client.search_people(domains=["acme.com"])

        jane = result.contacts[0]
        assert jane.id == "apl_001"
        assert jane.first_name == "Jane"
        assert jane.last_name == "Smith"
        assert jane.email == "jane@acme.com"
        assert jane.title == "VP Engineering"
        assert jane.company_name == "Acme Corp"
        assert jane.company_domain == "acme.com"
        assert jane.linkedin_url == "https://linkedin.com/in/janesmith"

    @respx.mock
    @pytest.mark.asyncio
    async def test_phone_extracted_from_sanitized_phone(self, apollo_client, apollo_fixtures):
        """sanitized_phone maps to contact.phone field."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        result = await apollo_client.search_people()

        jane = result.contacts[0]
        assert jane.phone == "+1234567890"

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_results(self, apollo_client, apollo_fixtures):
        """Empty results return an empty contacts list with total=0."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search_empty"])
        )
        result = await apollo_client.search_people(titles=["Unknown Title"])

        assert result.contacts == []
        assert result.total == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_contact_without_phone_has_none(self, apollo_client, apollo_fixtures):
        """Contact with no sanitized_phone has phone=None."""
        respx.post("https://api.apollo.io/v1/mixed_people/api_search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["people_search"])
        )
        result = await apollo_client.search_people()

        john = result.contacts[1]
        assert john.phone is None


class TestSearchOrganizations:
    """Tests for ApolloClient.search_organizations()."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_basic_search_parses_orgs(self, apollo_client, apollo_fixtures):
        """Organizations are correctly parsed from API response."""
        respx.post("https://api.apollo.io/v1/mixed_companies/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_search"])
        )
        result = await apollo_client.search_organizations(industries=["Software"])

        assert isinstance(result, OrgSearchResult)
        assert len(result.organizations) == 1
        assert result.total == 30

    @respx.mock
    @pytest.mark.asyncio
    async def test_org_fields_parsed_correctly(self, apollo_client, apollo_fixtures):
        """Organization fields map correctly including domain, headcount, funding."""
        respx.post("https://api.apollo.io/v1/mixed_companies/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_search"])
        )
        result = await apollo_client.search_organizations()

        acme = result.organizations[0]
        assert acme.id == "org_001"
        assert acme.name == "Acme Corp"
        assert acme.domain == "acme.com"
        assert acme.industry == "Software"
        assert acme.estimated_headcount == 500
        assert acme.funding_stage == "Series B"
        assert acme.funding_total == 45000000
        assert acme.description == "Developer tools company"
        assert acme.linkedin_url == "https://linkedin.com/company/acme"

    @respx.mock
    @pytest.mark.asyncio
    async def test_tech_stack_extracted_from_technologies(self, apollo_client, apollo_fixtures):
        """technologies list of objects is parsed into a list of name strings."""
        respx.post("https://api.apollo.io/v1/mixed_companies/search").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_search"])
        )
        result = await apollo_client.search_organizations()

        acme = result.organizations[0]
        assert acme.tech_stack == ["React", "PostgreSQL"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_headcount_range_sent_in_payload(self, apollo_client, apollo_fixtures):
        """min_headcount and max_headcount produce correct payload field."""
        captured_body = {}

        def capture(request):
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json=apollo_fixtures["org_search"])

        respx.post("https://api.apollo.io/v1/mixed_companies/search").mock(side_effect=capture)
        await apollo_client.search_organizations(min_headcount=100, max_headcount=1000)

        assert "organization_num_employees_ranges" in captured_body
        assert captured_body["organization_num_employees_ranges"] == ["100,1000"]


# ---------------------------------------------------------------------------
# Task 5: Person and Organization Enrichment Tests
# ---------------------------------------------------------------------------


class TestEnrichPerson:
    """Tests for ApolloClient.enrich_person()."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_by_email(self, apollo_client, apollo_fixtures):
        """Enrichment by email returns a populated ApolloContact."""
        respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["person_enrichment"])
        )
        contact = await apollo_client.enrich_person(email="alice@gamma.dev")

        assert isinstance(contact, ApolloContact)
        assert contact.id == "apl_003"
        assert contact.first_name == "Alice"
        assert contact.last_name == "Chen"
        assert contact.email == "alice@gamma.dev"
        assert contact.title == "Staff Engineer"
        assert contact.company_name == "Gamma Dev"
        assert contact.company_domain == "gamma.dev"
        assert contact.phone == "+9876543210"

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_by_linkedin_url(self, apollo_client, apollo_fixtures):
        """Enrichment by LinkedIn URL returns a populated ApolloContact."""
        respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["person_enrichment"])
        )
        contact = await apollo_client.enrich_person(linkedin_url="https://linkedin.com/in/alicechen")

        assert contact is not None
        assert contact.linkedin_url == "https://linkedin.com/in/alicechen"

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_person_not_found_returns_none(self, apollo_client, apollo_fixtures):
        """When person is null in response, returns None."""
        respx.post("https://api.apollo.io/v1/people/match").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["person_enrichment_not_found"])
        )
        contact = await apollo_client.enrich_person(email="nobody@nowhere.com")

        assert contact is None

    @pytest.mark.asyncio
    async def test_enrich_person_no_args_raises(self, apollo_client):
        """Calling with neither email nor linkedin_url raises ValueError."""
        with pytest.raises(ValueError):
            await apollo_client.enrich_person()


class TestEnrichOrganization:
    """Tests for ApolloClient.enrich_organization()."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_by_domain(self, apollo_client, apollo_fixtures):
        """Enrichment by domain returns a populated ApolloOrganization."""
        respx.post("https://api.apollo.io/v1/organizations/enrich").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_enrichment"])
        )
        org = await apollo_client.enrich_organization(domain="delta.systems")

        assert isinstance(org, ApolloOrganization)
        assert org.id == "org_002"
        assert org.name == "Delta Systems"
        assert org.domain == "delta.systems"
        assert org.industry == "Cloud Infrastructure"
        assert org.estimated_headcount == 1200
        assert org.funding_stage == "Series C"
        assert org.funding_total == 120000000
        assert org.description == "Cloud infrastructure provider"

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_org_tech_stack(self, apollo_client, apollo_fixtures):
        """Tech stack is extracted from technologies objects."""
        respx.post("https://api.apollo.io/v1/organizations/enrich").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_enrichment"])
        )
        org = await apollo_client.enrich_organization(domain="delta.systems")

        assert org.tech_stack == ["Kubernetes", "Go", "Terraform"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_org_not_found_returns_none(self, apollo_client, apollo_fixtures):
        """When organization is null in response, returns None."""
        respx.post("https://api.apollo.io/v1/organizations/enrich").mock(
            return_value=httpx.Response(200, json=apollo_fixtures["org_enrichment_not_found"])
        )
        org = await apollo_client.enrich_organization(domain="ghost.io")

        assert org is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_enrich_org_domain_sent_in_payload(self, apollo_client, apollo_fixtures):
        """The domain is sent as payload to the enrich endpoint."""
        captured_body = {}

        def capture(request):
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json=apollo_fixtures["org_enrichment"])

        respx.post("https://api.apollo.io/v1/organizations/enrich").mock(side_effect=capture)
        await apollo_client.enrich_organization(domain="delta.systems")

        assert captured_body.get("domain") == "delta.systems"
