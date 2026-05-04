# tests/test_pax_apollo.py
"""Tests for Pax Apollo.io integration."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.pax import Pax
from devrel_swarm.tools.apollo_client import ApolloContact, PeopleSearchResult


@pytest.fixture
def mock_apollo():
    client = MagicMock()
    client.search_people = AsyncMock(
        return_value=PeopleSearchResult(
            contacts=[
                ApolloContact(
                    id="apl_001",
                    first_name="Jane",
                    last_name="Smith",
                    email="jane@acme.com",
                    title="VP Engineering",
                    company_name="Acme Corp",
                    company_domain="acme.com",
                    linkedin_url="https://linkedin.com/in/janesmith",
                    phone="+1234567890",
                ),
                ApolloContact(
                    id="apl_002",
                    first_name="John",
                    last_name="Doe",
                    email="john@beta.io",
                    title="CTO",
                    company_name="Beta Inc",
                    company_domain="beta.io",
                ),
            ],
            total=2,
            page=1,
            per_page=25,
        )
    )
    client.enrich_person = AsyncMock(return_value=None)
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
    posthog_client,
    knowledge_base_path,
    mock_llm_client,
    mock_apollo,
    mock_instantly,
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
        self,
        pax_with_apollo,
        mock_apollo,
        mock_instantly,
    ):
        contacts = [
            ApolloContact(
                id="apl_001",
                first_name="Jane",
                last_name="Smith",
                email="jane@acme.com",
                title="VP Engineering",
                company_name="Acme Corp",
                company_domain="acme.com",
            ),
            ApolloContact(
                id="apl_002",
                first_name="John",
                last_name="Doe",
                email="john@beta.io",
                title="CTO",
                company_name="Beta Inc",
                company_domain="beta.io",
            ),
        ]
        result = await pax_with_apollo.enrich_and_upload(contacts, "camp_apollo_1")
        assert result["total_found"] == 2
        assert result["enriched"] == 0
        assert result["uploaded"] == 2
        mock_instantly.add_leads_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_contacts_without_email(
        self,
        pax_with_apollo,
        mock_apollo,
        mock_instantly,
    ):
        contacts = [
            ApolloContact(
                id="a1",
                first_name="No",
                last_name="Email",
                linkedin_url="https://linkedin.com/in/noemail",
            ),
            ApolloContact(
                id="a2",
                first_name="Has",
                last_name="Email",
                email="has@email.com",
            ),
        ]
        # enrich_person returns None — enrichment attempted but fails
        mock_apollo.enrich_person = AsyncMock(return_value=None)
        mock_instantly.add_leads_bulk = AsyncMock(
            return_value={"added": 1, "skipped": 0},
        )
        result = await pax_with_apollo.enrich_and_upload(contacts)
        assert result["skipped_no_email"] == 1
        assert result["uploaded"] == 1
        mock_apollo.enrich_person.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrichment_recovers_email(
        self,
        pax_with_apollo,
        mock_apollo,
        mock_instantly,
    ):
        contacts = [
            ApolloContact(
                id="a1",
                first_name="No",
                last_name="Email",
                linkedin_url="https://linkedin.com/in/noemail",
            ),
        ]
        # enrich_person returns a contact WITH email
        mock_apollo.enrich_person = AsyncMock(
            return_value=ApolloContact(
                id="a1_enriched",
                first_name="No",
                last_name="Email",
                email="recovered@acme.com",
                linkedin_url="https://linkedin.com/in/noemail",
            )
        )
        mock_instantly.add_leads_bulk = AsyncMock(
            return_value={"added": 1, "skipped": 0},
        )
        result = await pax_with_apollo.enrich_and_upload(contacts)
        assert result["enriched"] == 1
        assert result["skipped_no_email"] == 0
        assert result["uploaded"] == 1

    @pytest.mark.asyncio
    async def test_enrichment_skips_no_linkedin(
        self,
        pax_with_apollo,
        mock_apollo,
        mock_instantly,
    ):
        contacts = [
            ApolloContact(
                id="a1",
                first_name="No",
                last_name="Email",
                # no email AND no linkedin_url
            ),
        ]
        mock_apollo.enrich_person = AsyncMock(return_value=None)
        result = await pax_with_apollo.enrich_and_upload(contacts)
        assert result["skipped_no_email"] == 1
        assert result["enriched"] == 0
        mock_apollo.enrich_person.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_instantly_client(self, pax_apollo_only):
        contacts = [
            ApolloContact(
                id="a1",
                first_name="Jane",
                last_name="S",
                email="jane@co.com",
            ),
        ]
        result = await pax_apollo_only.enrich_and_upload(contacts)
        assert result["uploaded"] == 0
        assert result["enriched"] == 0
        assert result["total_found"] == 1

    @pytest.mark.asyncio
    async def test_batch_splitting(self, pax_with_apollo, mock_instantly):
        contacts = [
            ApolloContact(
                id=f"a{i}",
                first_name=f"User{i}",
                last_name="Test",
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
        self,
        pax_with_apollo,
        mock_llm_client,
        mock_apollo,
        mock_instantly,
    ):
        mock_llm_client.generate = AsyncMock(
            return_value=json.dumps(
                {
                    "title": "VP Engineering",
                    "industry": "Software",
                }
            )
        )
        result = await pax_with_apollo.execute(
            "Prospect and find leads matching our ICP",
        )
        assert result["agent"] == "pax"
        assert result["asset_type"] == "prospect_leads"

    @pytest.mark.asyncio
    async def test_enrich_upload_execute(
        self,
        pax_with_apollo,
        mock_llm_client,
        mock_apollo,
        mock_instantly,
    ):
        result = await pax_with_apollo.execute(
            "Enrich and upload contacts to campaign",
            context={
                "apollo_contacts": [
                    {"id": "a1", "first_name": "J", "last_name": "S", "email": "j@co.com"},
                ]
            },
        )
        assert result["agent"] == "pax"
        assert result["asset_type"] == "enrich_upload"
