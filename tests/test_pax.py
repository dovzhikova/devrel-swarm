"""Tests for Pax sales enablement agent."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from devrel_swarm.core.pax import (
    BattleCard,
    NurtureSequence,
    OutreachEmail,
    Pax,
    PersonalizedOutreach,
    SalesAsset,
)
from devrel_swarm.tools.apollo_client import ApolloContact
from devrel_swarm.tools.search_tools import SearchResult


@pytest.fixture
def pax(posthog_client, knowledge_base_path, mock_llm_client):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
    )


@pytest.fixture
def pax_no_llm(posthog_client, knowledge_base_path):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
    )


class TestPaxDataclasses:
    """Test dataclass construction."""

    def test_outreach_email(self):
        email = OutreachEmail(
            subject="Improve your AI assistant workflow",
            body="Hi {name},\n\nI noticed you're evaluating...",
            personalization_hooks=["uses Botpress currently"],
            pain_points_addressed=["channel integration complexity"],
            cta="Book a 15-min demo",
        )
        assert email.cta == "Book a 15-min demo"

    def test_battle_card(self):
        card = BattleCard(
            competitor="Botpress",
            comparison_table={"channels": {"us": "15+", "them": "5"}},
            objection_responses=[{"objection": "Botpress is free", "response": "OpenClaw is also open-source"}],
            win_themes=["More channels", "Better privacy"],
            proof_points=["500+ GitHub stars"],
        )
        assert card.competitor == "Botpress"

    def test_nurture_sequence(self):
        seq = NurtureSequence(
            segment="trial-users",
            goal="Convert trial to paid",
            cadence_days=[0, 3, 7, 14, 21],
            emails=[],
        )
        assert len(seq.cadence_days) == 5

    def test_sales_asset(self):
        asset = SalesAsset(
            title="OpenClaw for Enterprise",
            asset_type="one-pager",
            body="OpenClaw is...",
            target_persona="CTO",
            target_vertical="devtools",
        )
        assert asset.asset_type == "one-pager"

    def test_personalized_outreach(self):
        outreach = PersonalizedOutreach(
            contact_id="abc123",
            first_name="Jane",
            last_name="Smith",
            email="jane@acme.dev",
            title="Head of DevRel",
            company_name="Acme DevTools",
            research_hook="Just raised Series B",
            research_source="https://techcrunch.com/acme",
            subject="Saw your Series B",
            body="Hi Jane...",
            pain_points_addressed=["scaling devrel"],
            sales_psychology="Value Equation",
        )
        assert outreach.contact_id == "abc123"
        assert outreach.email == "jane@acme.dev"
        assert outreach.sales_psychology == "Value Equation"


class TestPaxTaskParsing:
    """Test _parse_asset_type() keyword matching."""

    def test_outreach_email(self, pax):
        assert pax._parse_asset_type("Generate outreach emails for DevOps engineers") == "outreach"

    def test_battle_card(self, pax):
        assert pax._parse_asset_type("Create a battle card: OpenClaw vs Botpress") == "battle_card"

    def test_nurture_sequence(self, pax):
        assert pax._parse_asset_type("Write a 5-email nurture sequence for trial users") == "nurture"

    def test_one_pager(self, pax):
        assert pax._parse_asset_type("Create a one-pager for enterprise CTOs") == "one_pager"

    def test_objection_doc(self, pax):
        assert pax._parse_asset_type("Write objection handling doc") == "objection"

    def test_vs_keyword(self, pax):
        assert pax._parse_asset_type("OpenClaw vs Rasa comparison") == "battle_card"

    def test_default_fallback(self, pax):
        assert pax._parse_asset_type("Create something useful for sales") == "general"

    def test_prospect_personalize_find_leads(self, pax):
        assert pax._parse_asset_type(
            "Find 15 leads and personalize outreach for DevRel leaders"
        ) == "prospect_personalize"

    def test_prospect_personalize_prospect_and(self, pax):
        assert pax._parse_asset_type(
            "Prospect and personalize emails for VP Engineering"
        ) == "prospect_personalize"

    def test_prospect_personalize_personalized_outreach(self, pax):
        assert pax._parse_asset_type(
            "Send personalized outreach to DevTools founders"
        ) == "prospect_personalize"

    def test_prospect_leads_without_personalize(self, pax):
        assert pax._parse_asset_type(
            "Find leads matching our ICP at Series B companies"
        ) == "prospect_leads"

    def test_outreach_without_personalize(self, pax):
        assert pax._parse_asset_type(
            "Write an outreach email to a Head of DevRel"
        ) == "outreach"


class TestPaxUpstreamContext:
    """Test _extract_upstream_context()."""

    def test_extracts_rex_competitive(self, pax):
        context = {
            "rex_competitive": {
                "profiles": [{"name": "Botpress", "strengths": ["visual builder"]}],
                "threats": [{"competitor": "Rasa", "threat": "growing", "severity": "medium"}],
            },
        }
        extracted = pax._extract_upstream_context(context)
        assert len(extracted["competitors"]) == 1
        assert len(extracted["threats"]) == 1

    def test_extracts_iris_pain_points(self, pax):
        context = {
            "iris_themes": {
                "themes": [
                    {"title": "Channel setup complexity", "severity": 7.0, "description": "Hard to connect"},
                ],
            },
        }
        extracted = pax._extract_upstream_context(context)
        assert len(extracted["pain_points"]) == 1

    def test_handles_empty_context(self, pax):
        extracted = pax._extract_upstream_context(None)
        assert extracted["competitors"] == []
        assert extracted["pain_points"] == []
        assert extracted["issues"] == []


class TestPaxExecute:
    """Test execute() entry point."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, pax):
        result = await pax.execute("Generate outreach emails for DevOps engineers")
        assert result["agent"] == "pax"
        assert result["asset_type"] == "outreach"
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm(self, pax_no_llm):
        result = await pax_no_llm.execute("Create a battle card: OpenClaw vs Botpress")
        assert result["agent"] == "pax"
        assert result["asset_type"] == "battle_card"
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_with_upstream_context(self, pax):
        context = {
            "rex_competitive": {
                "profiles": [{"name": "Botpress", "strengths": ["visual builder"]}],
            },
            "iris_themes": {
                "themes": [{"title": "Setup complexity", "severity": 7.0}],
            },
        }
        result = await pax.execute("Generate outreach emails", context=context)
        assert result["agent"] == "pax"


@pytest.fixture
def mock_search_tools():
    """Fixture providing mocked SearchTools."""
    tools = MagicMock()
    tools.web_search = AsyncMock(return_value=[])
    tools.fetch_url_content = AsyncMock(return_value="")
    return tools


@pytest.fixture
def pax_with_search(posthog_client, knowledge_base_path, mock_llm_client, mock_search_tools):
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        search_tools=mock_search_tools,
    )


@pytest.fixture
def sample_contact():
    return ApolloContact(
        id="apollo_123",
        first_name="Jane",
        last_name="Smith",
        email="jane@acme.dev",
        title="Head of Developer Relations",
        company_name="Acme DevTools",
        linkedin_url="https://linkedin.com/in/janesmith",
    )


class TestResearchProspect:
    """Test _research_prospect() web research."""

    @pytest.mark.asyncio
    async def test_returns_empty_without_search_tools(
        self, posthog_client, knowledge_base_path, mock_llm_client, sample_contact,
    ):
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
        )
        hook, url = await pax._research_prospect(sample_contact)
        assert hook == ""
        assert url == ""

    @pytest.mark.asyncio
    async def test_extracts_hook_from_web_search(
        self, pax_with_search, mock_search_tools, mock_llm_client, sample_contact,
    ):
        mock_search_tools.web_search.return_value = [
            SearchResult(
                title="Acme DevTools raises Series B",
                url="https://techcrunch.com/acme-series-b",
                snippet="Acme DevTools announced $30M Series B...",
                source="web",
            ),
        ]
        mock_search_tools.fetch_url_content.return_value = (
            "Acme DevTools announced a $30M Series B round led by Sequoia. "
            "The company plans to expand its developer tools platform."
        )
        mock_llm_client.generate.return_value = (
            "Acme DevTools just raised $30M Series B led by Sequoia"
        )

        hook, url = await pax_with_search._research_prospect(sample_contact)
        assert "30M" in hook
        assert url == "https://techcrunch.com/acme-series-b"

    @pytest.mark.asyncio
    async def test_returns_snippet_when_no_llm(
        self, posthog_client, knowledge_base_path, mock_search_tools, sample_contact,
    ):
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search_tools,
        )
        mock_search_tools.web_search.return_value = [
            SearchResult(
                title="Acme news",
                url="https://example.com",
                snippet="Acme launched new product",
                source="web",
            ),
        ]
        mock_search_tools.fetch_url_content.return_value = "Some content"

        hook, url = await pax._research_prospect(sample_contact)
        assert hook == "Acme launched new product"
        assert url == "https://example.com"

    @pytest.mark.asyncio
    async def test_fallback_to_company_search(
        self, pax_with_search, mock_search_tools, mock_llm_client, sample_contact,
    ):
        mock_search_tools.web_search.side_effect = [
            [],
            [SearchResult(
                title="Acme news",
                url="https://example.com/acme",
                snippet="Acme announced product launch",
                source="web",
            )],
        ]
        mock_search_tools.fetch_url_content.return_value = "Acme launched DevTools Pro"
        mock_llm_client.generate.return_value = "Acme recently launched DevTools Pro"

        hook, url = await pax_with_search._research_prospect(sample_contact)
        assert hook == "Acme recently launched DevTools Pro"
        assert mock_search_tools.web_search.call_count == 2

    @pytest.mark.asyncio
    async def test_no_hook_returns_empty(
        self, pax_with_search, mock_search_tools, mock_llm_client, sample_contact,
    ):
        mock_search_tools.web_search.return_value = [
            SearchResult(title="x", url="https://x.com", snippet="x", source="web"),
        ]
        mock_search_tools.fetch_url_content.return_value = "Irrelevant content"
        mock_llm_client.generate.return_value = "NO_HOOK"

        hook, url = await pax_with_search._research_prospect(sample_contact)
        assert hook == ""
        assert url == ""

    @pytest.mark.asyncio
    async def test_empty_search_results(
        self, pax_with_search, mock_search_tools, sample_contact,
    ):
        mock_search_tools.web_search.return_value = []

        contact = ApolloContact(
            id="x", first_name="John", last_name="Doe",
        )
        hook, url = await pax_with_search._research_prospect(contact)
        assert hook == ""
        assert url == ""
        assert mock_search_tools.web_search.call_count == 1


class TestPaxConstructor:
    """Test Pax constructor with new search_tools param."""

    def test_search_tools_defaults_to_none(self, posthog_client, knowledge_base_path):
        pax = Pax(api_client=posthog_client, knowledge_base_path=knowledge_base_path)
        assert pax.search_tools is None

    def test_search_tools_accepted(self, posthog_client, knowledge_base_path):
        mock_search = MagicMock()
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            search_tools=mock_search,
        )
        assert pax.search_tools is mock_search


class TestGeneratePersonalizedEmail:
    """Test _generate_personalized_email() LLM-based email generation."""

    @pytest.mark.asyncio
    async def test_generates_email_with_hook(
        self, pax_with_search, mock_llm_client, sample_contact,
    ):
        mock_llm_client.generate.return_value = json.dumps({
            "subject": "Saw Acme's Series B — congrats!",
            "body": "Hi Jane, congrats on the raise...",
            "pain_points_addressed": ["scaling DevRel"],
            "sales_psychology": "Value Equation",
        })

        result = await pax_with_search._generate_personalized_email(
            contact=sample_contact,
            research_hook="Acme just raised $30M Series B",
            kb_context="Product does X and Y",
            competitive_context="- Competitor A: strong in Z",
        )

        assert result is not None
        assert result["subject"] == "Saw Acme's Series B — congrats!"
        assert "pain_points_addressed" in result

    @pytest.mark.asyncio
    async def test_generates_email_without_hook(
        self, pax_with_search, mock_llm_client, sample_contact,
    ):
        mock_llm_client.generate.return_value = json.dumps({
            "subject": "DevRel at Acme DevTools",
            "body": "Hi Jane, as Head of DevRel...",
            "pain_points_addressed": ["content production"],
            "sales_psychology": "Risk Reversal",
        })

        result = await pax_with_search._generate_personalized_email(
            contact=sample_contact,
            research_hook="",
            kb_context="Product info",
            competitive_context="",
        )

        assert result is not None
        assert "subject" in result

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(
        self, pax_with_search, mock_llm_client, sample_contact,
    ):
        mock_llm_client.generate.return_value = "This is not JSON at all"

        result = await pax_with_search._generate_personalized_email(
            contact=sample_contact,
            research_hook="hook",
            kb_context="kb",
            competitive_context="comp",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_llm(
        self, posthog_client, knowledge_base_path, sample_contact,
    ):
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
        )

        result = await pax._generate_personalized_email(
            contact=sample_contact,
            research_hook="hook",
            kb_context="kb",
            competitive_context="comp",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_markdown_fenced_json(
        self, pax_with_search, mock_llm_client, sample_contact,
    ):
        mock_llm_client.generate.return_value = (
            '```json\n{"subject": "test", "body": "hi", '
            '"pain_points_addressed": [], "sales_psychology": "none"}\n```'
        )

        result = await pax_with_search._generate_personalized_email(
            contact=sample_contact,
            research_hook="hook",
            kb_context="kb",
            competitive_context="comp",
        )

        assert result is not None
        assert result["subject"] == "test"


@pytest.fixture
def mock_apollo_client():
    """Fixture providing mocked Apollo client."""
    from devrel_swarm.tools.apollo_client import ApolloContact, PeopleSearchResult

    client = MagicMock()
    client.search_people = AsyncMock(return_value=PeopleSearchResult(
        contacts=[
            ApolloContact(
                id="c1", first_name="Jane", last_name="Smith",
                email="jane@acme.dev", title="Head of DevRel",
                company_name="Acme DevTools",
                linkedin_url="https://linkedin.com/in/janesmith",
            ),
            ApolloContact(
                id="c2", first_name="Bob", last_name="Lee",
                email="bob@beta.io", title="VP Developer Experience",
                company_name="Beta Platform",
            ),
            ApolloContact(
                id="c3", first_name="No", last_name="Email",
                email=None, title="CTO", company_name="Ghost Inc",
                linkedin_url="https://linkedin.com/in/noemail",
            ),
        ],
        total=3, page=1, per_page=25,
    ))
    client.enrich_person = AsyncMock(return_value=None)
    return client


@pytest.fixture
def pax_full(
    posthog_client, knowledge_base_path, mock_llm_client,
    mock_search_tools, mock_apollo_client,
):
    """Pax with all clients wired up."""
    return Pax(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        search_tools=mock_search_tools,
        apollo_client=mock_apollo_client,
        product_name="TestProduct",
    )


class TestExecuteProspectPersonalize:
    """Test _execute_prospect_personalize() full flow."""

    @pytest.mark.asyncio
    async def test_full_flow_returns_outreach(
        self, pax_full, mock_llm_client, mock_search_tools,
    ):
        # LLM call 1: ICP extraction
        # LLM call 2+: research hooks
        # LLM call 3+: email generation
        mock_llm_client.generate.side_effect = [
            # ICP extraction
            '{"titles": ["Head of DevRel"], "industries": ["software"]}',
            # Research hook for Jane
            "Acme DevTools just launched a new CLI tool",
            # Email for Jane
            json.dumps({
                "subject": "Saw your new CLI launch",
                "body": "Hi Jane...",
                "pain_points_addressed": ["scaling devrel"],
                "sales_psychology": "Value Equation",
            }),
            # Research hook for Bob
            "NO_HOOK",
            # Email for Bob (no hook)
            json.dumps({
                "subject": "DevRel at Beta Platform",
                "body": "Hi Bob...",
                "pain_points_addressed": ["content"],
                "sales_psychology": "Risk Reversal",
            }),
        ]
        mock_search_tools.web_search.return_value = [
            SearchResult(
                title="News", url="https://news.com/acme",
                snippet="Acme launched CLI", source="web",
            ),
        ]
        mock_search_tools.fetch_url_content.return_value = "Acme launched CLI tool"

        result = await pax_full.execute(
            "Find 15 leads and personalize outreach for DevRel leaders"
        )

        assert result["status"] == "personalized"
        assert result["contacts_found"] == 3
        assert result["contacts_with_email"] == 2
        assert result["skipped_no_email"] == 1
        assert result["emails_generated"] == 2
        assert len(result["outreach"]) == 2
        assert result["outreach"][0]["email"] == "jane@acme.dev"
        assert result["outreach"][0]["subject"] == "Saw your new CLI launch"

    @pytest.mark.asyncio
    async def test_returns_early_with_no_contacts(
        self, pax_full, mock_llm_client, mock_apollo_client,
    ):
        from devrel_swarm.tools.apollo_client import PeopleSearchResult

        mock_apollo_client.search_people.return_value = PeopleSearchResult(
            contacts=[], total=0, page=1, per_page=25,
        )
        mock_llm_client.generate.return_value = '{"titles": ["CTO"]}'

        result = await pax_full.execute(
            "Find leads and personalize outreach for CTOs"
        )

        assert result["status"] == "personalized"
        assert result["contacts_found"] == 0

    @pytest.mark.asyncio
    async def test_skips_contacts_without_email(
        self, pax_full, mock_llm_client, mock_apollo_client, mock_search_tools,
    ):
        from devrel_swarm.tools.apollo_client import ApolloContact, PeopleSearchResult

        # All contacts missing email, enrichment fails
        mock_apollo_client.search_people.return_value = PeopleSearchResult(
            contacts=[
                ApolloContact(
                    id="c1", first_name="No", last_name="Email",
                    linkedin_url="https://linkedin.com/in/x",
                ),
            ],
            total=1, page=1, per_page=25,
        )
        mock_apollo_client.enrich_person.return_value = None
        mock_llm_client.generate.return_value = '{"titles": ["CTO"]}'

        result = await pax_full.execute(
            "Find leads and personalize outreach for CTOs"
        )

        assert result["contacts_found"] == 1
        assert result["contacts_with_email"] == 0
        assert result["skipped_no_email"] == 1
        assert result["emails_generated"] == 0

    @pytest.mark.asyncio
    async def test_handles_email_generation_failure(
        self, pax_full, mock_llm_client, mock_search_tools, mock_apollo_client,
    ):
        from devrel_swarm.tools.apollo_client import ApolloContact, PeopleSearchResult

        mock_apollo_client.search_people.return_value = PeopleSearchResult(
            contacts=[
                ApolloContact(
                    id="c1", first_name="Jane", last_name="S",
                    email="jane@x.com", title="CTO", company_name="X",
                ),
            ],
            total=1, page=1, per_page=25,
        )
        # ICP extraction succeeds, research hook succeeds, email gen fails
        mock_llm_client.generate.side_effect = [
            '{"titles": ["CTO"]}',
            "Hook about X",
            "NOT VALID JSON",  # email gen fails
        ]
        mock_search_tools.web_search.return_value = [
            SearchResult(title="x", url="https://x.com", snippet="x", source="web"),
        ]
        mock_search_tools.fetch_url_content.return_value = "content"

        result = await pax_full.execute(
            "Find leads and personalize outreach for CTOs"
        )

        assert result["contacts_found"] == 1
        assert result["emails_generated"] == 0

    @pytest.mark.asyncio
    async def test_falls_through_without_apollo_client(
        self, posthog_client, knowledge_base_path, mock_llm_client, mock_search_tools,
    ):
        """Without apollo_client, execute() falls through to generic asset gen."""
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            search_tools=mock_search_tools,
        )

        result = await pax.execute(
            "Find leads and personalize outreach for CTOs"
        )

        # Falls through to generic generation, not prospect_personalize
        assert result["asset_type"] == "prospect_personalize"
        assert result["status"] == "generated"
        assert "contacts_found" not in result

    @pytest.mark.asyncio
    async def test_generates_emails_without_search_tools(
        self, posthog_client, knowledge_base_path, mock_llm_client, mock_apollo_client,
    ):
        """Without search_tools, emails generate with empty research hooks."""
        pax = Pax(
            api_client=posthog_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=mock_llm_client,
            apollo_client=mock_apollo_client,
            product_name="TestProduct",
        )
        from devrel_swarm.tools.apollo_client import ApolloContact, PeopleSearchResult

        mock_apollo_client.search_people.return_value = PeopleSearchResult(
            contacts=[
                ApolloContact(
                    id="c1", first_name="Jane", last_name="S",
                    email="jane@x.com", title="CTO", company_name="X",
                ),
            ],
            total=1, page=1, per_page=25,
        )
        mock_llm_client.generate.side_effect = [
            '{"titles": ["CTO"]}',
            # No research hook call (no search_tools)
            json.dumps({
                "subject": "DevTools at X",
                "body": "Hi Jane...",
                "pain_points_addressed": ["scaling"],
                "sales_psychology": "Value Equation",
            }),
        ]

        result = await pax.execute(
            "Find leads and personalize outreach for CTOs"
        )

        assert result["status"] == "personalized"
        assert result["emails_generated"] == 1
        assert result["hooks_found"] == 0
        assert result["outreach"][0]["research_hook"] == ""
