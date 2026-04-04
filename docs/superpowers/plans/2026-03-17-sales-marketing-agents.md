# Sales & Marketing Agents Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three new agents (Rex, Pax, Mox) for competitive intelligence, sales enablement, and campaign marketing to the existing multi-agent system.

**Architecture:** Rex runs weekly in the Atlas pipeline (Stage 2b, after Echo/Sage). Pax and Mox are on-demand, triggered via CLI. All three follow the existing agent pattern: dataclass DTOs, `async execute(task, context)` interface, knowledge base search, LLM generation with graceful degradation.

**Tech Stack:** Python 3.12+, async/await, dataclasses, httpx, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-17-sales-marketing-agents-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `agents/rex.py` | Competitive Intelligence agent — competitor discovery, web search, threat/opportunity analysis |
| `agents/pax.py` | Sales Enablement agent — outreach emails, battle cards, nurture sequences, objection docs |
| `agents/mox.py` | Campaign Marketing agent — blog posts, landing page copy, social batches, press releases |
| `agents/atlas.py` | (Modify) Add 3 SharedContext fields, register 3 agents, add Rex to weekly cycle, update OKRs and `to_dict()` |
| `agents/__init__.py` | (Modify) Export Rex, Pax, Mox |
| `tests/test_rex.py` | Unit tests for Rex |
| `tests/test_pax.py` | Unit tests for Pax |
| `tests/test_mox.py` | Unit tests for Mox |

---

## Chunk 1: Rex — Competitive Intelligence Agent

### Task 1: Rex dataclasses and constructor

**Files:**
- Create: `agents/rex.py`
- Test: `tests/test_rex.py`

- [ ] **Step 1: Write failing tests for Rex dataclasses**

```python
# tests/test_rex.py
"""Tests for Rex competitive intelligence agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.rex import (
    CompetitorProfile,
    CompetitiveReport,
    MarketPosition,
    Opportunity,
    Rex,
    Threat,
)


class TestRexDataclasses:
    """Test dataclass construction."""

    def test_competitor_profile(self):
        profile = CompetitorProfile(
            name="Botpress",
            domain="botpress.com",
            category="chatbot-platform",
            strengths=["visual flow builder", "self-hosted option"],
            weaknesses=["limited LLM support"],
            recent_moves=["launched v13 with GPT integration"],
        )
        assert profile.name == "Botpress"
        assert len(profile.strengths) == 2

    def test_market_position(self):
        pos = MarketPosition(
            competitor="Botpress",
            positioning_statement="Open-source chatbot builder",
            differentiators=["visual builder", "on-prem deployment"],
            pricing_tier="freemium",
            target_audience="enterprise IT teams",
        )
        assert pos.pricing_tier == "freemium"

    def test_threat(self):
        t = Threat(competitor="Rasa", threat="Open-source NLU gaining traction", severity="high")
        assert t.severity == "high"

    def test_opportunity(self):
        o = Opportunity(gap="No competitor offers voice + chat unification", recommendation="Emphasize multi-modal")
        assert "voice" in o.gap

    def test_competitive_report(self):
        report = CompetitiveReport(
            profiles=[],
            market_positions=[],
            threats=[],
            opportunities=[],
            recommended_responses=[],
        )
        assert isinstance(report.profiles, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_rex.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.rex'`

- [ ] **Step 3: Create Rex with dataclasses and constructor**

```python
# agents/rex.py
"""
Rex -- Competitive Intelligence Agent

Weekly competitive landscape monitoring. Identifies what competitors are doing,
where they're strong/weak, and what the target product should do about it.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agents.llm import LLMClient
from tools.api_client import PostHogClient
from tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


@dataclass
class CompetitorProfile:
    """A tracked competitor and their current market position."""

    name: str
    domain: str
    category: str
    strengths: list[str]
    weaknesses: list[str]
    recent_moves: list[str]


@dataclass
class MarketPosition:
    """How a competitor positions themselves."""

    competitor: str
    positioning_statement: str
    differentiators: list[str]
    pricing_tier: str
    target_audience: str


@dataclass
class Threat:
    """A competitive threat."""

    competitor: str
    threat: str
    severity: str


@dataclass
class Opportunity:
    """A competitive gap/opportunity."""

    gap: str
    recommendation: str


@dataclass
class CompetitiveReport:
    """Weekly competitive intelligence output."""

    profiles: list[CompetitorProfile]
    market_positions: list[MarketPosition]
    threats: list[Threat]
    opportunities: list[Opportunity]
    recommended_responses: list[str]


class Rex:
    """
    Competitive Intelligence agent for market monitoring.

    Capabilities:
    - Discover competitors from knowledge base and task string
    - Search web for competitor news and GitHub activity
    - Cross-reference Echo's social mentions and Sage's issues
    - Build competitor profiles with strengths/weaknesses
    - Identify threats and opportunities
    - Generate narrative competitive report via LLM
    """

    SYSTEM_PROMPT = """You are Rex, a competitive intelligence analyst for {product_name}. \
Your role is to monitor the competitive landscape and produce actionable intelligence that \
informs sales positioning, product strategy, and marketing messaging.

You produce:
- Weekly competitive landscape reports
- Competitor profiles with strengths/weaknesses
- Threat assessments with severity ratings
- Opportunity identification with recommended responses

Ground all analysis in evidence: social mentions, GitHub activity, web search \
results, and knowledge base comparisons. Never speculate without data."""

    COMPETITOR_KEYWORDS = ["vs", "alternative", "compared to", "competitor", "versus"]

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        search_tools: Optional[SearchTools] = None,
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.search_tools = search_tools
        self.product_name = product_name
        self._kb_index = self._build_kb_index()

    def _build_kb_index(self) -> dict[str, Path]:
        """Index all knowledge base files for search."""
        index = {}
        if self.knowledge_base_path.exists():
            for file in self.knowledge_base_path.rglob("*.md"):
                key = file.stem.lower().replace("-", " ").replace("_", " ")
                index[key] = file
        return index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_rex.py::TestRexDataclasses -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/rex.py tests/test_rex.py
git commit -m "feat: add Rex competitive intelligence agent — dataclasses and constructor"
```

---

### Task 2: Rex competitor discovery and upstream context extraction

**Files:**
- Modify: `agents/rex.py`
- Test: `tests/test_rex.py`

- [ ] **Step 1: Write failing tests for competitor discovery**

Add to `tests/test_rex.py`:

```python
@pytest.fixture
def mock_search_tools():
    st = MagicMock()
    st.web_search = AsyncMock(return_value=[])
    st.close = AsyncMock()
    return st


@pytest.fixture
def rex_kb_with_competitors(tmp_path):
    """KB with files that mention competitors."""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    (kb / "features").mkdir()
    (kb / "features" / "messaging.md").write_text(
        "# Messaging\nOpenClaw vs Botpress: OpenClaw supports more channels.\n"
        "Alternative to Rasa for conversational AI."
    )
    (kb / "features" / "voice.md").write_text(
        "# Voice\nVoice support compared to Voiceflow.\n"
    )
    return kb


@pytest.fixture
def rex(posthog_client, rex_kb_with_competitors, mock_search_tools, mock_llm_client):
    return Rex(
        api_client=posthog_client,
        knowledge_base_path=rex_kb_with_competitors,
        llm_client=mock_llm_client,
        search_tools=mock_search_tools,
    )


@pytest.fixture
def rex_no_tools(posthog_client, rex_kb_with_competitors):
    return Rex(
        api_client=posthog_client,
        knowledge_base_path=rex_kb_with_competitors,
    )


class TestCompetitorDiscovery:
    """Test _discover_competitors()."""

    def test_finds_competitors_from_kb(self, rex):
        competitors = rex._discover_competitors("")
        assert "Botpress" in competitors
        assert "Rasa" in competitors
        assert "Voiceflow" in competitors

    def test_parses_competitors_from_task(self, rex):
        task = "Competitive analysis for: Botpress, Rasa, Voiceflow"
        competitors = rex._discover_competitors(task)
        assert "Botpress" in competitors
        assert "Rasa" in competitors
        assert "Voiceflow" in competitors

    def test_deduplicates(self, rex):
        task = "Competitive analysis for: Botpress"
        competitors = rex._discover_competitors(task)
        assert competitors.count("Botpress") == 1


class TestUpstreamContext:
    """Test _extract_upstream_context()."""

    def test_extracts_echo_competitor_mentions(self, rex):
        context = {
            "echo_social": {
                "top_mentions": [
                    {"title": "Botpress vs OpenClaw", "platform": "reddit", "sentiment": "positive"},
                    {"title": "Rasa pricing change", "platform": "hackernews", "sentiment": "neutral"},
                ],
            },
        }
        extracted = rex._extract_upstream_context(context)
        assert len(extracted["social_mentions"]) == 2

    def test_extracts_sage_competitor_issues(self, rex):
        context = {
            "sage_triage": {
                "issues": [
                    {"number": 101, "title": "Support Botpress migration", "category": "feature"},
                ],
            },
        }
        extracted = rex._extract_upstream_context(context)
        assert len(extracted["github_issues"]) == 1

    def test_handles_empty_context(self, rex):
        extracted = rex._extract_upstream_context(None)
        assert extracted["social_mentions"] == []
        assert extracted["github_issues"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_rex.py::TestCompetitorDiscovery tests/test_rex.py::TestUpstreamContext -v`
Expected: FAIL — `AttributeError: 'Rex' object has no attribute '_discover_competitors'`

- [ ] **Step 3: Implement competitor discovery and context extraction**

Add to `agents/rex.py` inside the `Rex` class:

```python
    def _discover_competitors(self, task: str) -> list[str]:
        """Discover competitor names from KB content and task string."""
        competitors: set[str] = set()

        # Parse from task string: "Competitive analysis for: X, Y, Z"
        if "for:" in task.lower():
            after_for = task.split("for:", 1)[1] if "for:" in task else ""
            if not after_for:
                after_for = task.split("For:", 1)[1] if "For:" in task else ""
            names = [n.strip() for n in after_for.split(",") if n.strip()]
            competitors.update(names)

        # Scan KB files for competitor mentions
        for _key, path in self._kb_index.items():
            try:
                content = path.read_text()
            except Exception:
                continue
            for keyword in self.COMPETITOR_KEYWORDS:
                if keyword in content.lower():
                    # Extract capitalized words near the keyword
                    for line in content.splitlines():
                        if keyword in line.lower():
                            words = line.split()
                            for word in words:
                                cleaned = word.strip(".,;:()\"'")
                                if (
                                    cleaned
                                    and cleaned[0].isupper()
                                    and len(cleaned) > 2
                                    and cleaned.lower() not in {
                                        "openclaw", "open", "the", "for",
                                        "voice", "alternative", "compared",
                                    }
                                    and not cleaned.startswith("#")
                                ):
                                    competitors.add(cleaned)

        return sorted(competitors)

    def _extract_upstream_context(
        self, context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Extract competitor-relevant data from SharedContext."""
        extracted: dict[str, Any] = {
            "social_mentions": [],
            "github_issues": [],
        }
        if not context:
            return extracted

        # Echo social mentions
        if "echo_social" in context:
            echo = context["echo_social"]
            if isinstance(echo, dict):
                extracted["social_mentions"] = echo.get("top_mentions", [])

        # Sage GitHub issues
        if "sage_triage" in context:
            sage = context["sage_triage"]
            if isinstance(sage, dict):
                extracted["github_issues"] = sage.get("issues", [])

        return extracted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_rex.py::TestCompetitorDiscovery tests/test_rex.py::TestUpstreamContext -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add agents/rex.py tests/test_rex.py
git commit -m "feat: Rex competitor discovery from KB and upstream context extraction"
```

---

### Task 3: Rex execute method

**Files:**
- Modify: `agents/rex.py`
- Test: `tests/test_rex.py`

- [ ] **Step 1: Write failing tests for execute**

Add to `tests/test_rex.py`:

```python
class TestRexExecute:
    """Test execute() entry point."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, rex):
        result = await rex.execute("Analyze competitive landscape")
        assert result["agent"] == "rex"
        assert "profiles" in result
        assert "threats" in result
        assert "opportunities" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm(self, rex_no_tools):
        result = await rex_no_tools.execute("Analyze competitive landscape")
        assert result["agent"] == "rex"
        assert result["status"] == "analyzed"
        assert "prompt_used" in result  # graceful degradation

    @pytest.mark.asyncio
    async def test_execute_finds_competitors(self, rex):
        result = await rex.execute("Competitive analysis for: Botpress, Rasa")
        assert "Botpress" in result.get("competitors_analyzed", [])
        assert "Rasa" in result.get("competitors_analyzed", [])

    @pytest.mark.asyncio
    async def test_execute_with_upstream_context(self, rex):
        context = {
            "echo_social": {
                "top_mentions": [
                    {"title": "Botpress rising", "platform": "reddit", "sentiment": "neutral"},
                ],
            },
            "sage_triage": {
                "issues": [
                    {"number": 42, "title": "Support Rasa migration", "category": "feature"},
                ],
            },
        }
        result = await rex.execute("Analyze competitive landscape", context=context)
        assert result["agent"] == "rex"

    @pytest.mark.asyncio
    async def test_execute_with_web_search_failure(self, rex, mock_search_tools):
        """Graceful degradation when web search fails."""
        mock_search_tools.web_search = AsyncMock(side_effect=Exception("Network error"))
        result = await rex.execute("Analyze competitive landscape")
        assert result["agent"] == "rex"
        assert result["status"] == "analyzed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_rex.py::TestRexExecute -v`
Expected: FAIL — `AttributeError: 'Rex' object has no attribute 'execute'` (or `execute` not yet implemented)

- [ ] **Step 3: Implement execute method**

Add to `agents/rex.py` inside the `Rex` class:

```python
    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a competitive intelligence task.

        Steps:
        1. Discover competitors from KB and task string
        2. For each competitor, search web for recent activity
        3. Cross-reference upstream social/issue data
        4. Generate narrative report via LLM (if available)
        5. Return structured dict for SharedContext
        """
        logger.info(f"Rex executing: {task[:80]}...")

        competitors = self._discover_competitors(task)
        upstream = self._extract_upstream_context(context)

        # Gather web intelligence per competitor
        web_intel: dict[str, list[str]] = {}
        if self.search_tools:
            for comp in competitors:
                try:
                    results = await self.search_tools.web_search(
                        f"{comp} product news announcements 2026", limit=5,
                    )
                    web_intel[comp] = [
                        f"{r.title}: {r.snippet}" for r in results
                    ]
                except Exception as exc:
                    logger.warning(f"Web search failed for {comp}: {exc}")
                    web_intel[comp] = []

        # Build grounding prompt for LLM
        kb_context = self._search_knowledge_base(task)
        social_section = ""
        if upstream["social_mentions"]:
            social_section = "Social mentions:\n" + "\n".join(
                f"- [{m.get('platform', '')}] {m.get('title', '')}"
                for m in upstream["social_mentions"][:10]
            )

        issues_section = ""
        if upstream["github_issues"]:
            issues_section = "Related GitHub issues:\n" + "\n".join(
                f"- #{i.get('number', '?')}: {i.get('title', '')}"
                for i in upstream["github_issues"][:10]
            )

        web_section = ""
        if web_intel:
            web_section = "Web intelligence:\n"
            for comp, items in web_intel.items():
                web_section += f"\n### {comp}\n"
                for item in items[:3]:
                    web_section += f"- {item}\n"

        prompt = f"""Task: {task}

## Competitors to analyze
{', '.join(competitors) if competitors else 'No competitors discovered. Analyze the general market.'}

## Knowledge Base Context
{kb_context if kb_context else 'No relevant KB documents found.'}

## Upstream Data
{social_section}

{issues_section}

{web_section}

## Instructions
For each competitor, produce:
1. A CompetitorProfile (name, domain, category, strengths, weaknesses, recent moves)
2. A MarketPosition (positioning statement, differentiators, pricing tier, target audience)

Then identify:
3. Threats — where competitors are gaining ground (with severity: high/medium/low)
4. Opportunities — gaps we can exploit (with specific recommendations)
5. Recommended responses — 3-5 concrete actions

Return a JSON object with keys: profiles, market_positions, threats, opportunities,
recommended_responses. Return ONLY valid JSON, no markdown fences."""

        base_result: dict[str, Any] = {
            "agent": "rex",
            "task": task,
            "competitors_analyzed": competitors,
            "status": "analyzed",
        }

        if self.llm_client:
            try:
                raw = await self.llm_client.generate(
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=prompt,
                    temperature=0.4,
                    max_tokens=4096,
                )
                import json
                import re

                cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), count=1)
                cleaned = re.sub(r"\n?```\s*$", "", cleaned)
                data = json.loads(cleaned.strip())
                base_result.update({
                    "profiles": data.get("profiles", []),
                    "market_positions": data.get("market_positions", []),
                    "threats": data.get("threats", []),
                    "opportunities": data.get("opportunities", []),
                    "recommended_responses": data.get("recommended_responses", []),
                })
            except Exception as exc:
                logger.warning(f"LLM generation failed: {exc}")
                base_result["prompt_used"] = prompt[:500]
        else:
            base_result["prompt_used"] = prompt[:500]

        return base_result

    def _search_knowledge_base(self, query: str, max_results: int = 5) -> str:
        """Search KB for relevant docs. Returns concatenated content."""
        query_terms = set(query.lower().split())
        stop_words = {
            "the", "a", "an", "is", "for", "and", "or", "of", "to", "in",
            "analyze", "competitive", "landscape", "analysis", "market",
        }
        query_terms -= stop_words
        results = []

        for key, path in self._kb_index.items():
            try:
                content = path.read_text()
            except Exception:
                continue
            content_lower = content.lower()
            key_terms = set(key.split())
            score = len(query_terms & key_terms) * 2 + sum(
                1 for t in query_terms if t in content_lower
            )
            if score > 0:
                results.append((score, path.name, content[:2000]))

        results.sort(key=lambda x: x[0], reverse=True)

        # Fallback: return all KB docs if nothing matched
        if not results:
            for _key, path in self._kb_index.items():
                try:
                    content = path.read_text()
                except Exception:
                    continue
                results.append((0, path.name, content[:2000]))
                if len(results) >= max_results:
                    break

        return "\n\n".join(
            f"[{name}]\n{content}" for _, name, content in results[:max_results]
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_rex.py -v`
Expected: PASS (all Rex tests)

- [ ] **Step 5: Commit**

```bash
git add agents/rex.py tests/test_rex.py
git commit -m "feat: Rex execute method with web search, LLM generation, graceful degradation"
```

---

## Chunk 2: Pax — Sales Enablement Agent

### Task 4: Pax dataclasses, constructor, and task parsing

**Files:**
- Create: `agents/pax.py`
- Test: `tests/test_pax.py`

- [ ] **Step 1: Write failing tests for Pax dataclasses and task parsing**

```python
# tests/test_pax.py
"""Tests for Pax sales enablement agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.pax import (
    BattleCard,
    NurtureSequence,
    OutreachEmail,
    Pax,
    SalesAsset,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_pax.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.pax'`

- [ ] **Step 3: Create Pax with dataclasses, constructor, and task parsing**

```python
# agents/pax.py
"""
Pax -- Sales Enablement Agent

On-demand sales asset generation: outreach emails, battle cards,
nurture sequences, objection handling docs, and one-pagers.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agents.llm import LLMClient
from tools.api_client import PostHogClient

logger = logging.getLogger(__name__)


@dataclass
class OutreachEmail:
    """A personalized outreach email."""

    subject: str
    body: str
    personalization_hooks: list[str]
    pain_points_addressed: list[str]
    cta: str


@dataclass
class BattleCard:
    """One-page competitive comparison document."""

    competitor: str
    comparison_table: dict[str, dict[str, str]]
    objection_responses: list[dict[str, str]]
    win_themes: list[str]
    proof_points: list[str]


@dataclass
class NurtureSequence:
    """Multi-step email drip campaign."""

    segment: str
    goal: str
    cadence_days: list[int]
    emails: list[OutreachEmail]


@dataclass
class SalesAsset:
    """Generic sales document."""

    title: str
    asset_type: str
    body: str
    target_persona: str
    target_vertical: str


class Pax:
    """
    Sales Enablement agent for on-demand asset generation.

    Capabilities:
    - Outreach emails personalized with community pain points
    - Battle cards grounded in Rex's competitive intelligence
    - Nurture sequences for different audience segments
    - One-pagers and objection handling docs
    """

    SYSTEM_PROMPT = """You are Pax, a sales enablement specialist for {product_name}. \
Your role is to produce sales assets that help close deals: outreach emails, battle cards, \
nurture sequences, one-pagers, and objection handling docs.

Guidelines:
1. EVIDENCE-BASED -- Ground every claim in knowledge base facts, competitive \
data, or real community pain points. No empty marketing speak.
2. DEVELOPER-AWARE -- The buyer is often a developer or technical leader. \
Respect their intelligence. Lead with value, not hype.
3. PERSONALIZED -- Use upstream pain points and competitive gaps to make \
outreach specific and relevant to the recipient's situation.
4. ACTIONABLE -- Every asset should have a clear CTA and next step.
5. HONEST -- Never misrepresent capabilities. Acknowledge limitations when \
they exist -- credibility matters more than closing one deal."""

    ASSET_KEYWORDS: dict[str, list[str]] = {
        "outreach": ["outreach", "email", "prospect"],
        "battle_card": ["battle card", "vs", "comparison"],
        "nurture": ["nurture", "drip", "sequence"],
        "one_pager": ["one-pager", "one pager", "summary"],
        "objection": ["objection", "faq", "pushback"],
    }

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.product_name = product_name
        self._kb_index = self._build_kb_index()

    def _build_kb_index(self) -> dict[str, Path]:
        """Index all knowledge base files for search."""
        index = {}
        if self.knowledge_base_path.exists():
            for file in self.knowledge_base_path.rglob("*.md"):
                key = file.stem.lower().replace("-", " ").replace("_", " ")
                index[key] = file
        return index

    def _parse_asset_type(self, task: str) -> str:
        """Determine asset type from task string via keyword matching."""
        task_lower = task.lower()
        for asset_type, keywords in self.ASSET_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                return asset_type
        return "general"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_pax.py -v`
Expected: PASS (all Pax tests)

- [ ] **Step 5: Commit**

```bash
git add agents/pax.py tests/test_pax.py
git commit -m "feat: add Pax sales enablement agent — dataclasses, constructor, task parsing"
```

---

### Task 5: Pax execute method

**Files:**
- Modify: `agents/pax.py`
- Test: `tests/test_pax.py`

- [ ] **Step 1: Write failing tests for Pax execute**

Add to `tests/test_pax.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_pax.py::TestPaxUpstreamContext tests/test_pax.py::TestPaxExecute -v`
Expected: FAIL

- [ ] **Step 3: Implement upstream context extraction and execute method**

Add to `agents/pax.py` inside the `Pax` class:

```python
    def _extract_upstream_context(
        self, context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Extract sales-relevant data from SharedContext."""
        extracted: dict[str, Any] = {
            "competitors": [],
            "threats": [],
            "pain_points": [],
            "issues": [],
        }
        if not context:
            return extracted

        # Rex competitive data
        if "rex_competitive" in context:
            rex = context["rex_competitive"]
            if isinstance(rex, dict):
                extracted["competitors"] = rex.get("profiles", [])
                extracted["threats"] = rex.get("threats", [])

        # Iris pain points
        if "iris_themes" in context:
            iris = context["iris_themes"]
            if isinstance(iris, dict):
                extracted["pain_points"] = iris.get("themes", [])

        # Sage issues
        if "sage_triage" in context:
            sage = context["sage_triage"]
            if isinstance(sage, dict):
                extracted["issues"] = sage.get("issues", [])

        return extracted

    def _search_knowledge_base(self, query: str, max_results: int = 5) -> str:
        """Search KB for relevant docs. Returns concatenated content."""
        query_terms = set(query.lower().split())
        stop_words = {
            "the", "a", "an", "is", "for", "and", "or", "of", "to", "in",
            "generate", "create", "write", "outreach", "emails", "battle",
            "card", "nurture", "sequence", "one-pager",
        }
        query_terms -= stop_words
        results = []

        for key, path in self._kb_index.items():
            try:
                content = path.read_text()
            except Exception:
                continue
            content_lower = content.lower()
            key_terms = set(key.split())
            score = len(query_terms & key_terms) * 2 + sum(
                1 for t in query_terms if t in content_lower
            )
            if score > 0:
                results.append((score, path.name, content[:2000]))

        results.sort(key=lambda x: x[0], reverse=True)
        if not results:
            for _key, path in self._kb_index.items():
                try:
                    content = path.read_text()
                except Exception:
                    continue
                results.append((0, path.name, content[:2000]))
                if len(results) >= max_results:
                    break

        return "\n\n".join(
            f"[{name}]\n{content}" for _, name, content in results[:max_results]
        )

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a sales enablement task.

        Determines asset type from task string, gathers upstream context,
        and generates the asset via LLM.
        """
        logger.info(f"Pax executing: {task[:80]}...")

        asset_type = self._parse_asset_type(task)
        upstream = self._extract_upstream_context(context)
        kb_context = self._search_knowledge_base(task)

        # Build competitive section for battle cards
        competitive_section = ""
        if upstream["competitors"]:
            competitive_section = "Competitor profiles:\n"
            for c in upstream["competitors"][:5]:
                if isinstance(c, dict):
                    competitive_section += (
                        f"- {c.get('name', '?')}: "
                        f"strengths={c.get('strengths', [])}, "
                        f"weaknesses={c.get('weaknesses', [])}\n"
                    )

        # Build pain points section
        pain_section = ""
        if upstream["pain_points"]:
            pain_section = "Developer pain points:\n"
            for pp in upstream["pain_points"][:5]:
                if isinstance(pp, dict):
                    pain_section += (
                        f"- {pp.get('title', '?')} "
                        f"(severity: {pp.get('severity', '?')}): "
                        f"{pp.get('description', '')[:200]}\n"
                    )

        prompt = f"""Task: {task}
Asset type: {asset_type}

## Knowledge Base
{kb_context if kb_context else 'No relevant KB docs found.'}

## Competitive Intelligence
{competitive_section if competitive_section else 'No competitive data available.'}

## Developer Pain Points
{pain_section if pain_section else 'No pain point data available.'}

## Instructions
Generate the requested sales asset ({asset_type}). Ground all claims in the
knowledge base and competitive data above. Include specific features, real
pain points, and concrete CTAs. Do NOT invent capabilities not in the KB.

Return a JSON object with the generated asset content."""

        base_result: dict[str, Any] = {
            "agent": "pax",
            "task": task,
            "asset_type": asset_type,
            "status": "generated",
        }

        if self.llm_client:
            try:
                raw = await self.llm_client.generate(
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=prompt,
                    temperature=0.5,
                    max_tokens=4096,
                )
                base_result["content"] = raw
            except Exception as exc:
                logger.warning(f"LLM generation failed: {exc}")
                base_result["prompt_used"] = prompt[:500]
        else:
            base_result["prompt_used"] = prompt[:500]

        return base_result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_pax.py -v`
Expected: PASS (all Pax tests)

- [ ] **Step 5: Commit**

```bash
git add agents/pax.py tests/test_pax.py
git commit -m "feat: Pax execute method with upstream context and LLM generation"
```

---

## Chunk 3: Mox — Campaign Marketing Agent

### Task 6: Mox dataclasses, constructor, and task parsing

**Files:**
- Create: `agents/mox.py`
- Test: `tests/test_mox.py`

- [ ] **Step 1: Write failing tests for Mox dataclasses and task parsing**

```python
# tests/test_mox.py
"""Tests for Mox campaign marketing agent."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.mox import (
    BlogPost,
    CampaignBrief,
    LandingPageCopy,
    Mox,
    PressRelease,
    SocialBatch,
)


@pytest.fixture
def mock_search_tools():
    st = MagicMock()
    st.web_search = AsyncMock(return_value=[])
    st.close = AsyncMock()
    return st


@pytest.fixture
def mox(posthog_client, knowledge_base_path, mock_llm_client, mock_search_tools):
    return Mox(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
        llm_client=mock_llm_client,
        search_tools=mock_search_tools,
    )


@pytest.fixture
def mox_no_llm(posthog_client, knowledge_base_path):
    return Mox(
        api_client=posthog_client,
        knowledge_base_path=knowledge_base_path,
    )


class TestMoxDataclasses:
    """Test dataclass construction."""

    def test_blog_post(self):
        post = BlogPost(
            title="Best Open-Source AI Assistants in 2026",
            body="Content here...",
            meta_description="Compare top AI assistants.",
            target_keywords=["ai assistant", "open source"],
            cta="Try OpenClaw free",
            word_count=1200,
        )
        assert post.word_count == 1200

    def test_landing_page_copy(self):
        lp = LandingPageCopy(
            hero_headline="Your AI, Your Rules",
            hero_subhead="Run locally, connect everywhere.",
            features=[{"title": "Multi-channel", "description": "15+ integrations"}],
            social_proof=["500+ stars on GitHub"],
            cta_primary="Get Started Free",
            cta_secondary="See Demo",
            seo_title="OpenClaw - Open Source AI Assistant",
            seo_description="Run AI on your own devices.",
        )
        assert lp.hero_headline == "Your AI, Your Rules"

    def test_social_batch(self):
        batch = SocialBatch(
            platform="twitter",
            campaign_name="Launch week",
            posts=[{"text": "Announcing...", "hook": "hook", "cta": "Try it"}],
            hashtags=["#OpenClaw", "#AIAssistant"],
        )
        assert batch.platform == "twitter"

    def test_campaign_brief(self):
        brief = CampaignBrief(
            name="Voice Launch",
            goal="Drive awareness",
            positioning="First open-source voice-enabled assistant",
            messages=["primary msg", "secondary msg"],
            channels=["twitter", "blog"],
            timeline=[{"day": "1", "action": "Blog post", "owner": "Mox"}],
            draft_assets=["blog post", "social batch"],
        )
        assert brief.name == "Voice Launch"

    def test_press_release(self):
        pr = PressRelease(
            headline="OpenClaw 1.0 Released",
            subhead="Open-source AI assistant goes GA",
            body="Content...",
            quotes=[{"speaker": "CEO", "title": "Founder", "quote": "We're excited"}],
            boilerplate="OpenClaw is...",
            contact="press@openclaw.ai",
        )
        assert pr.headline == "OpenClaw 1.0 Released"


class TestMoxTaskParsing:
    """Test _parse_content_type() keyword matching."""

    def test_blog_post(self, mox):
        assert mox._parse_content_type("Write an SEO blog post about AI assistants") == "blog"

    def test_landing_page(self, mox):
        assert mox._parse_content_type("Write landing page copy for WhatsApp integration") == "landing_page"

    def test_social_batch(self, mox):
        assert mox._parse_content_type("Generate social media posts for Twitter") == "social"

    def test_campaign_brief(self, mox):
        assert mox._parse_content_type("Create a product launch campaign") == "campaign"

    def test_press_release(self, mox):
        assert mox._parse_content_type("Write a press release for 1.0") == "press_release"

    def test_announcement_maps_to_press_release(self, mox):
        assert mox._parse_content_type("Write an announcement for the new feature") == "press_release"

    def test_case_study(self, mox):
        assert mox._parse_content_type("Create a case study framework for DevOps") == "case_study"

    def test_linkedin_maps_to_social(self, mox):
        assert mox._parse_content_type("Write LinkedIn posts for the team") == "social"

    def test_default_fallback(self, mox):
        assert mox._parse_content_type("Create some marketing content") == "blog"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_mox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.mox'`

- [ ] **Step 3: Create Mox with dataclasses, constructor, and task parsing**

```python
# agents/mox.py
"""
Mox -- Campaign Marketing Agent

On-demand marketing content and campaign generation: SEO blog posts,
landing page copy, social media batches, launch campaigns, and press releases.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agents.llm import LLMClient
from tools.api_client import PostHogClient
from tools.code_validator import CodeValidator
from tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


@dataclass
class BlogPost:
    """SEO-optimized marketing blog post."""

    title: str
    body: str
    meta_description: str
    target_keywords: list[str]
    cta: str
    word_count: int


@dataclass
class LandingPageCopy:
    """Full landing page copy structure."""

    hero_headline: str
    hero_subhead: str
    features: list[dict[str, str]]
    social_proof: list[str]
    cta_primary: str
    cta_secondary: str
    seo_title: str
    seo_description: str


@dataclass
class SocialBatch:
    """A batch of platform-specific social media posts."""

    platform: str
    campaign_name: str
    posts: list[dict[str, str]]
    hashtags: list[str]


@dataclass
class CampaignBrief:
    """Full product launch or marketing campaign brief."""

    name: str
    goal: str
    positioning: str
    messages: list[str]
    channels: list[str]
    timeline: list[dict[str, str]]
    draft_assets: list[str]


@dataclass
class PressRelease:
    """Structured press release."""

    headline: str
    subhead: str
    body: str
    quotes: list[dict[str, str]]
    boilerplate: str
    contact: str


class Mox:
    """
    Campaign Marketing agent for on-demand content generation.

    Capabilities:
    - SEO blog posts grounded in product knowledge and pain points
    - Landing page copy with features, social proof, and CTAs
    - Social media batches adapted to platform conventions
    - Product launch campaign briefs with timelines
    - Press releases for announcements
    """

    SYSTEM_PROMPT = """You are Mox, a campaign marketing specialist for {product_name}. \
Your role is to produce marketing content and campaigns that drive awareness, engagement, \
and conversion among developers and technical decision-makers.

Guidelines:
1. DEVELOPER-AUTHENTIC -- Write like a developer advocate, not a marketer. \
No buzzwords, no fluff. Technical audiences smell inauthenticity instantly.
2. SEO-AWARE -- Structure blog posts with clear H2/H3 hierarchy, include \
target keywords naturally, write compelling meta descriptions.
3. PAIN-POINT-DRIVEN -- Every piece of content should address a real developer \
frustration identified by upstream agents, not invented marketing problems.
4. DIFFERENTIATED -- Use competitive intelligence to position against \
alternatives. Show don't tell -- concrete features, not vague claims.
5. MULTI-FORMAT -- Adapt messaging for each platform's conventions. Twitter \
threads != LinkedIn posts != Reddit comments."""

    CONTENT_KEYWORDS: dict[str, list[str]] = {
        "blog": ["blog", "seo", "article"],
        "landing_page": ["landing page", "landing copy"],
        "social": ["social", "twitter", "linkedin", "reddit"],
        "campaign": ["launch", "campaign"],
        "press_release": ["press release", "announcement"],
        "case_study": ["case study", "customer story"],
    }

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        search_tools: Optional[SearchTools] = None,
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.search_tools = search_tools
        self.product_name = product_name
        self.code_validator = CodeValidator()
        self._kb_index = self._build_kb_index()

    def _build_kb_index(self) -> dict[str, Path]:
        """Index all knowledge base files for search."""
        index = {}
        if self.knowledge_base_path.exists():
            for file in self.knowledge_base_path.rglob("*.md"):
                key = file.stem.lower().replace("-", " ").replace("_", " ")
                index[key] = file
        return index

    def _parse_content_type(self, task: str) -> str:
        """Determine content type from task string via keyword matching."""
        task_lower = task.lower()
        for content_type, keywords in self.CONTENT_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                return content_type
        return "blog"  # default
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_mox.py -v`
Expected: PASS (all Mox tests)

- [ ] **Step 5: Commit**

```bash
git add agents/mox.py tests/test_mox.py
git commit -m "feat: add Mox campaign marketing agent — dataclasses, constructor, task parsing"
```

---

### Task 7: Mox execute method

**Files:**
- Modify: `agents/mox.py`
- Test: `tests/test_mox.py`

- [ ] **Step 1: Write failing tests for Mox execute**

Add to `tests/test_mox.py`:

```python
class TestMoxUpstreamContext:
    """Test _extract_upstream_context()."""

    def test_extracts_rex_competitive(self, mox):
        context = {
            "rex_competitive": {
                "profiles": [{"name": "Botpress", "strengths": ["visual builder"]}],
            },
        }
        extracted = mox._extract_upstream_context(context)
        assert len(extracted["competitors"]) == 1

    def test_extracts_iris_pain_points(self, mox):
        context = {
            "iris_themes": {
                "themes": [{"title": "Setup complexity", "severity": 7.0}],
            },
        }
        extracted = mox._extract_upstream_context(context)
        assert len(extracted["pain_points"]) == 1

    def test_extracts_kai_content(self, mox):
        context = {
            "kai_content": {
                "content": "Tutorial: How to set up voice channels",
                "grounding_sources": ["features/voice.md"],
            },
        }
        extracted = mox._extract_upstream_context(context)
        assert "Tutorial" in extracted["existing_content"]

    def test_handles_empty_context(self, mox):
        extracted = mox._extract_upstream_context(None)
        assert extracted["competitors"] == []
        assert extracted["pain_points"] == []
        assert extracted["existing_content"] == ""


class TestMoxExecute:
    """Test execute() entry point."""

    @pytest.mark.asyncio
    async def test_execute_returns_expected_structure(self, mox):
        result = await mox.execute("Write an SEO blog post about AI assistants")
        assert result["agent"] == "mox"
        assert result["content_type"] == "blog"
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_without_llm(self, mox_no_llm):
        result = await mox_no_llm.execute("Write landing page copy")
        assert result["agent"] == "mox"
        assert result["content_type"] == "landing_page"
        assert "prompt_used" in result

    @pytest.mark.asyncio
    async def test_execute_with_upstream_context(self, mox):
        context = {
            "rex_competitive": {
                "profiles": [{"name": "Botpress"}],
            },
            "iris_themes": {
                "themes": [{"title": "Setup pain", "severity": 8.0}],
            },
            "kai_content": {
                "content": "Tutorial content here",
            },
        }
        result = await mox.execute("Generate social media posts", context=context)
        assert result["agent"] == "mox"
        assert result["content_type"] == "social"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_mox.py::TestMoxUpstreamContext tests/test_mox.py::TestMoxExecute -v`
Expected: FAIL

- [ ] **Step 3: Implement upstream context extraction and execute method**

Add to `agents/mox.py` inside the `Mox` class:

```python
    def _extract_upstream_context(
        self, context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Extract marketing-relevant data from SharedContext."""
        extracted: dict[str, Any] = {
            "competitors": [],
            "pain_points": [],
            "existing_content": "",
        }
        if not context:
            return extracted

        # Rex competitive data
        if "rex_competitive" in context:
            rex = context["rex_competitive"]
            if isinstance(rex, dict):
                extracted["competitors"] = rex.get("profiles", [])

        # Iris pain points
        if "iris_themes" in context:
            iris = context["iris_themes"]
            if isinstance(iris, dict):
                extracted["pain_points"] = iris.get("themes", [])

        # Kai's existing content for repurposing
        if "kai_content" in context:
            kai = context["kai_content"]
            if isinstance(kai, dict):
                extracted["existing_content"] = kai.get("content", "")[:2000]

        return extracted

    def _search_knowledge_base(self, query: str, max_results: int = 5) -> str:
        """Search KB for relevant docs. Returns concatenated content."""
        query_terms = set(query.lower().split())
        stop_words = {
            "the", "a", "an", "is", "for", "and", "or", "of", "to", "in",
            "write", "generate", "create", "blog", "post", "landing", "page",
            "social", "media", "posts", "campaign", "press", "release",
        }
        query_terms -= stop_words
        results = []

        for key, path in self._kb_index.items():
            try:
                content = path.read_text()
            except Exception:
                continue
            content_lower = content.lower()
            key_terms = set(key.split())
            score = len(query_terms & key_terms) * 2 + sum(
                1 for t in query_terms if t in content_lower
            )
            if score > 0:
                results.append((score, path.name, content[:2000]))

        results.sort(key=lambda x: x[0], reverse=True)
        if not results:
            for _key, path in self._kb_index.items():
                try:
                    content = path.read_text()
                except Exception:
                    continue
                results.append((0, path.name, content[:2000]))
                if len(results) >= max_results:
                    break

        return "\n\n".join(
            f"[{name}]\n{content}" for _, name, content in results[:max_results]
        )

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a marketing content generation task.

        Determines content type from task string, gathers upstream context,
        and generates content via LLM.
        """
        logger.info(f"Mox executing: {task[:80]}...")

        content_type = self._parse_content_type(task)
        upstream = self._extract_upstream_context(context)
        kb_context = self._search_knowledge_base(task)

        # Build competitive section
        competitive_section = ""
        if upstream["competitors"]:
            competitive_section = "Competitive landscape:\n"
            for c in upstream["competitors"][:5]:
                if isinstance(c, dict):
                    competitive_section += (
                        f"- {c.get('name', '?')}: "
                        f"strengths={c.get('strengths', [])}\n"
                    )

        # Build pain points section
        pain_section = ""
        if upstream["pain_points"]:
            pain_section = "Developer pain points to address:\n"
            for pp in upstream["pain_points"][:5]:
                if isinstance(pp, dict):
                    pain_section += (
                        f"- {pp.get('title', '?')} "
                        f"(severity: {pp.get('severity', '?')})\n"
                    )

        # Existing content for repurposing
        existing_section = ""
        if upstream["existing_content"]:
            existing_section = (
                f"Existing tutorial content (for reference/repurposing):\n"
                f"{upstream['existing_content'][:1000]}"
            )

        prompt = f"""Task: {task}
Content type: {content_type}

## Knowledge Base
{kb_context if kb_context else 'No relevant KB docs found.'}

## Competitive Intelligence
{competitive_section if competitive_section else 'No competitive data available.'}

## Developer Pain Points
{pain_section if pain_section else 'No pain point data available.'}

{existing_section}

## Instructions
Generate the requested marketing content ({content_type}). Ground all claims
in the knowledge base. Address real developer pain points. Position against
competitors where relevant. Do NOT invent capabilities not in the KB.

For blog posts: include H2/H3 hierarchy, meta description, target keywords.
For social: adapt to platform conventions.
For landing pages: include hero, features, social proof, CTAs.

Return the content as markdown."""

        base_result: dict[str, Any] = {
            "agent": "mox",
            "task": task,
            "content_type": content_type,
            "status": "generated",
        }

        if self.llm_client:
            try:
                raw = await self.llm_client.generate(
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=prompt,
                    temperature=0.6,
                    max_tokens=6144,
                )
                base_result["content"] = raw

                # Validate code blocks in blog posts
                if content_type == "blog":
                    report = self.code_validator.validate_content(raw)
                    base_result["code_validation"] = {
                        "total_blocks": report.total_blocks,
                        "passed": report.passed,
                        "failed": report.failed,
                        "all_passed": report.all_passed,
                    }
            except Exception as exc:
                logger.warning(f"LLM generation failed: {exc}")
                base_result["prompt_used"] = prompt[:500]
        else:
            base_result["prompt_used"] = prompt[:500]

        return base_result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_mox.py -v`
Expected: PASS (all Mox tests)

- [ ] **Step 5: Commit**

```bash
git add agents/mox.py tests/test_mox.py
git commit -m "feat: Mox execute method with upstream context, LLM generation, code validation"
```

---

## Chunk 4: Atlas Integration

### Task 8: Update SharedContext, register agents, add Rex to weekly cycle

**Files:**
- Modify: `agents/atlas.py:33-64` (SharedContext)
- Modify: `agents/atlas.py:94-160` (Atlas.__init__ and _agents)
- Modify: `agents/atlas.py:215-301` (run_weekly_cycle)
- Modify: `agents/atlas.py:303-315` (_compile_okrs)
- Modify: `agents/__init__.py`
- Test: `tests/test_atlas.py` (add new tests)

- [ ] **Step 1: Read current test_atlas.py to understand existing patterns**

Run: `cat tests/test_atlas.py` (read to understand current test structure)

- [ ] **Step 2: Write failing tests for Atlas integration**

Add to `tests/test_atlas.py`:

```python
# Add these imports at the top:
from agents.rex import Rex
from agents.pax import Pax
from agents.mox import Mox


class TestAtlasSalesAgentRegistration:
    """Test that Rex, Pax, Mox are registered in Atlas."""

    def test_rex_registered(self, atlas):
        """atlas fixture is the existing Atlas fixture in test_atlas.py"""
        assert "rex" in atlas._agents
        assert isinstance(atlas._agents["rex"], Rex)

    def test_pax_registered(self, atlas):
        assert "pax" in atlas._agents
        assert isinstance(atlas._agents["pax"], Pax)

    def test_mox_registered(self, atlas):
        assert "mox" in atlas._agents
        assert isinstance(atlas._agents["mox"], Mox)


class TestSharedContextSalesFields:
    """Test SharedContext includes new sales/marketing fields."""

    def test_rex_competitive_field_exists(self):
        from agents.atlas import SharedContext
        ctx = SharedContext()
        assert hasattr(ctx, "rex_competitive")
        assert ctx.rex_competitive == {}

    def test_pax_sales_field_exists(self):
        from agents.atlas import SharedContext
        ctx = SharedContext()
        assert hasattr(ctx, "pax_sales")
        assert ctx.pax_sales == {}

    def test_mox_campaigns_field_exists(self):
        from agents.atlas import SharedContext
        ctx = SharedContext()
        assert hasattr(ctx, "mox_campaigns")
        assert ctx.mox_campaigns == {}

    def test_to_dict_includes_new_fields(self):
        from agents.atlas import SharedContext
        ctx = SharedContext()
        d = ctx.to_dict()
        assert "rex_competitive" in d
        assert "pax_sales" in d
        assert "mox_campaigns" in d


class TestAtlasWeeklyCycleRex:
    """Test Rex is called in the weekly cycle."""

    @pytest.mark.asyncio
    async def test_weekly_cycle_populates_rex_competitive(self, atlas):
        """Verify run_weekly_cycle calls Rex and populates context."""
        # Mock all agents to return quickly
        for name, agent in atlas._agents.items():
            agent.execute = AsyncMock(return_value={"agent": name, "status": "ok"})

        # Rex should return competitive data
        atlas._agents["rex"].execute = AsyncMock(return_value={
            "agent": "rex",
            "profiles": [{"name": "Botpress"}],
            "threats": [{"competitor": "Rasa", "severity": "medium"}],
            "opportunities": [],
        })

        context = await atlas.run_weekly_cycle()
        # Verify Rex was called
        atlas._agents["rex"].execute.assert_called_once()
        # Verify SharedContext was populated
        assert context.rex_competitive.get("profiles") is not None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd . && python -m pytest tests/test_atlas.py::TestAtlasSalesAgentRegistration tests/test_atlas.py::TestSharedContextSalesFields -v`
Expected: FAIL

- [ ] **Step 4: Update atlas.py — SharedContext**

In `agents/atlas.py`, add three new fields to `SharedContext` (after `dex_docs`, before `okr_progress`):

```python
    rex_competitive: dict[str, Any] = field(default_factory=dict)
    pax_sales: dict[str, Any] = field(default_factory=dict)
    mox_campaigns: dict[str, Any] = field(default_factory=dict)
```

Update `to_dict()` to include them:

```python
    def to_dict(self) -> dict[str, Any]:
        return {
            "week_of": self.week_of,
            "sage_triage": self.sage_triage,
            "echo_social": self.echo_social,
            "iris_themes": self.iris_themes,
            "nova_experiments": self.nova_experiments,
            "kai_content": self.kai_content,
            "vox_video": self.vox_video,
            "dex_docs": self.dex_docs,
            "rex_competitive": self.rex_competitive,
            "pax_sales": self.pax_sales,
            "mox_campaigns": self.mox_campaigns,
            "okr_progress": self.okr_progress,
        }
```

- [ ] **Step 5: Update atlas.py — imports and agent registration**

Add imports at top of `agents/atlas.py`:

```python
from agents.rex import Rex
from agents.pax import Pax
from agents.mox import Mox
```

In `Atlas.__init__()`, after `self.dex = Dex(...)`, add:

```python
        self.rex = Rex(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            search_tools=search_tools,
        )
        self.pax = Pax(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
        )
        self.mox = Mox(
            api_client=api_client,
            knowledge_base_path=knowledge_base_path,
            llm_client=llm_client,
            search_tools=search_tools,
        )
```

Update `self._agents` dict to include:

```python
            "rex": self.rex,
            "pax": self.pax,
            "mox": self.mox,
```

- [ ] **Step 6: Update atlas.py — weekly cycle and OKRs**

In `run_weekly_cycle()`, add Rex at Stage 2b (after Iris, before Nova):

```python
        # Stage 2b: Competitive intelligence (Rex) — uses Echo + Sage + web search
        rex_result = await self.delegate(
            "rex",
            "Analyze the competitive landscape. Identify competitor movements, "
            "threats, and opportunities based on social mentions and GitHub activity.",
        )
        if rex_result.success:
            self.context.rex_competitive = rex_result.output
```

Update `_compile_okrs()` to include Rex metrics:

```python
        "competitors_tracked": len(
            self.context.rex_competitive.get("profiles", [])
        ),
        "threats_identified": len(
            self.context.rex_competitive.get("threats", [])
        ),
        "opportunities_found": len(
            self.context.rex_competitive.get("opportunities", [])
        ),
```

- [ ] **Step 7: Update agents/__init__.py**

```python
from agents.rex import Rex
from agents.pax import Pax
from agents.mox import Mox

__all__ = ["Atlas", "Dex", "Echo", "Kai", "Mox", "Nova", "Pax", "Rex", "Sage", "Iris", "Vox"]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd . && python -m pytest tests/test_atlas.py -v`
Expected: PASS (all Atlas tests including new ones)

- [ ] **Step 9: Run all tests to verify nothing is broken**

Run: `cd . && python -m pytest tests/ -v --ignore=tests/test_integration.py`
Expected: PASS (all tests)

- [ ] **Step 10: Commit**

```bash
git add agents/atlas.py agents/__init__.py tests/test_atlas.py
git commit -m "feat: integrate Rex, Pax, Mox into Atlas pipeline and SharedContext"
```

---

### Task 9: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `cd . && python -m pytest tests/ -v --ignore=tests/test_integration.py`
Expected: ALL PASS

- [ ] **Step 2: Verify imports work**

Run:
```bash
cd . && python -c "
from agents import Rex, Pax, Mox, Atlas
from agents.rex import CompetitorProfile, CompetitiveReport, Threat, Opportunity
from agents.pax import OutreachEmail, BattleCard, NurtureSequence, SalesAsset
from agents.mox import BlogPost, LandingPageCopy, SocialBatch, CampaignBrief, PressRelease
print('All imports successful')
"
```
Expected: `All imports successful`

- [ ] **Step 3: Verify CLI help still works**

Run: `cd . && python -m agents.atlas --help`
Expected: Shows help with `--agent` and `--task` options

- [ ] **Step 4: Verify single agent execution works (dry run)**

Run:
```bash
cd . && python -c "
import asyncio
from pathlib import Path
from unittest.mock import MagicMock
from agents.rex import Rex

client = MagicMock()
rex = Rex(api_client=client, knowledge_base_path=Path('knowledge_base'))
result = asyncio.run(rex.execute('Test competitive analysis'))
print(f'Rex returned: agent={result[\"agent\"]}, status={result[\"status\"]}')
print(f'Competitors found: {result.get(\"competitors_analyzed\", [])}')
"
```
Expected: Rex returns successfully with agent=rex, status=analyzed
