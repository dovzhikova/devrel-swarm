"""
Rex — Competitive Intelligence Agent

Monitors the competitive landscape and produces actionable intelligence
that informs sales positioning, product strategy, and marketing messaging.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from devrel_origin.tools.apollo_client import ApolloClient

from devrel_origin.core.base import (
    STOP_WORDS,
    get_kb_search,
    load_agent_prompt,
    strip_markdown_fences,
)
from devrel_origin.core.llm import LLMClient
from devrel_origin.tools.api_client import PostHogClient
from devrel_origin.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)


# Keywords near which capitalised words are treated as competitor names
COMPETITOR_KEYWORDS = {"vs", "alternative", "compared to", "competitor", "versus"}

# Extra stop words specific to Rex (competitive analysis keywords)
REX_STOP_WORDS = frozenset(
    {
        "write",
        "technical",
        "tutorial",
        "addressing",
        "developer",
        "pain",
        "point",
        "analyze",
        "analyse",
        "competitive",
        "landscape",
        "report",
    }
)

# Cap parallel web-search and Apollo enrichment fan-out so a 10-competitor
# task doesn't open 10 simultaneous Firecrawl/Brave/Apollo connections.
# 3 is conservative enough for free-tier API limits while still cutting
# wall-clock time roughly to ceil(N/3).
SEARCH_CONCURRENCY = 3


def _guess_domain(comp: str) -> str:
    """Best-effort domain guess for a competitor name.

    Preserves an existing TLD if the input already looks like a domain
    (e.g. ``"Pendo.io"`` → ``"pendo.io"``, ``"FullStory"`` → ``"fullstory.com"``).
    Strips spaces; the result is lowercased so callers can pass it
    straight to Apollo enrichment.
    """
    cleaned = comp.strip().lower().replace(" ", "")
    # If the name already contains a dot followed by 2+ alpha chars, treat
    # it as an existing domain and don't append `.com` on top.
    if re.search(r"\.[a-z]{2,}$", cleaned):
        return cleaned
    return f"{cleaned}.com"


@dataclass
class CompetitorProfile:
    """A tracked competitor and their current market position."""

    name: str
    domain: str
    category: str  # e.g., "ai-assistant", "chatbot-platform"
    strengths: list[str]
    weaknesses: list[str]
    recent_moves: list[str]


@dataclass
class MarketPosition:
    """How a competitor positions themselves."""

    competitor: str
    positioning_statement: str
    differentiators: list[str]
    pricing_tier: str  # "free", "freemium", "paid", "enterprise"
    target_audience: str


@dataclass
class Threat:
    """A competitive threat."""

    competitor: str
    threat: str
    severity: str  # "high", "medium", "low"


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
    Competitive Intelligence agent for monitoring the competitive landscape.

    Capabilities:
    - Discover competitors from task descriptions and the knowledge base
    - Web-search each competitor for recent activity and positioning
    - Produce competitor profiles with strengths/weaknesses
    - Assess threats and opportunities with severity/impact ratings
    - Generate actionable competitive intelligence reports

    Tools:
    1. knowledge_base_search — Retrieve competitive mentions from docs
    2. web_search — Search the web for competitor activity
    3. llm_generate — Generate structured competitive analysis
    """

    _DEFAULT_SYSTEM_PROMPT = (
        "You are Rex, a competitive intelligence analyst for {product_name}. "
        "Your role is to monitor the competitive landscape and produce actionable "
        "intelligence that informs sales positioning, product strategy, and "
        "marketing messaging.\n\n"
        "You produce:\n"
        "- Weekly competitive landscape reports\n"
        "- Competitor profiles with strengths/weaknesses\n"
        "- Threat assessments with severity ratings\n"
        "- Opportunity identification with recommended responses\n\n"
        "Ground all analysis in evidence: social mentions, GitHub activity, "
        "web search results, and knowledge base comparisons. "
        "Never speculate without data."
    )

    @property
    def SYSTEM_PROMPT_TEMPLATE(self) -> str:
        return self._system_prompt_template

    @property
    def SYSTEM_PROMPT(self) -> str:
        return self._system_prompt

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        search_tools: Optional[SearchTools] = None,
        apollo_client: Optional["ApolloClient"] = None,  # NEW
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.search_tools = search_tools
        self.apollo_client = apollo_client  # NEW
        self.product_name = product_name
        self._kb = get_kb_search(
            knowledge_base_path,
            extra_stop_words=REX_STOP_WORDS,
        )
        self._system_prompt_template = load_agent_prompt(
            "rex", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT
        )
        self._system_prompt = self._system_prompt_template.format(product_name=self.product_name)

    # ------------------------------------------------------------------
    # Competitor discovery
    # ------------------------------------------------------------------

    def _discover_competitors(self, task: str) -> list[str]:
        """
        Discover competitor names from the task string and the knowledge base.

        Extraction sources:
        1. Explicit "for: X, Y, Z" pattern in the task string
        2. Capitalised words near competitor keywords in KB files
        """
        competitors: set[str] = set()

        # 1. Parse from task string: "for: X, Y, Z"
        match = re.search(r"for:\s*(.+)", task, re.IGNORECASE)
        if match:
            names = [n.strip() for n in match.group(1).split(",") if n.strip()]
            competitors.update(names)

        # 2. Scan knowledge base for capitalised words near competitor keywords
        for _key, path in self._kb.index.items():
            try:
                content = path.read_text()
            except Exception:
                continue
            content_lower = content.lower()
            for keyword in COMPETITOR_KEYWORDS:
                if keyword in content_lower:
                    # Capitalised word AFTER keyword: "vs Mixpanel"
                    after_pattern = r"(?i:" + re.escape(keyword) + r")\s+([A-Z][a-zA-Z]+)"
                    for m in re.finditer(after_pattern, content):
                        candidate = m.group(1).strip()
                        if (
                            len(candidate) > 1
                            and candidate.lower() not in STOP_WORDS
                            and candidate[0].isupper()
                        ):
                            competitors.add(candidate)

                    # Capitalised word BEFORE keyword: "Amplitude is an alternative"
                    before_pattern = (
                        r"([A-Z][a-zA-Z]+)\s+(?:\w+\s+)*?"
                        r"(?i:" + re.escape(keyword) + r")"
                    )
                    for m in re.finditer(before_pattern, content):
                        candidate = m.group(1).strip()
                        if (
                            len(candidate) > 1
                            and candidate.lower() not in STOP_WORDS
                            and candidate[0].isupper()
                        ):
                            competitors.add(candidate)

        return sorted(competitors)

    # ------------------------------------------------------------------
    # Upstream context extraction
    # ------------------------------------------------------------------

    def _extract_upstream_context(
        self,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Extract structured upstream context from SharedContext.

        Looks for:
        - echo_social.top_mentions — social chatter about competitors
        - sage_triage.issues — community-reported issues (churn signals, comparisons)
        """
        extracted: dict[str, Any] = {
            "social_mentions": [],
            "community_issues": [],
        }
        if not context:
            return extracted

        # Echo social mentions
        if "echo_social" in context:
            echo = context["echo_social"]
            if isinstance(echo, dict):
                for mention in echo.get("top_mentions", []):
                    if isinstance(mention, dict):
                        extracted["social_mentions"].append(
                            {
                                "platform": mention.get("platform", ""),
                                "title": mention.get("title", ""),
                                "sentiment": mention.get("sentiment", ""),
                                "url": mention.get("url", ""),
                            }
                        )

        # Sage triage issues
        if "sage_triage" in context:
            sage = context["sage_triage"]
            if isinstance(sage, dict):
                for issue in sage.get("issues", [])[:10]:
                    if isinstance(issue, dict):
                        extracted["community_issues"].append(
                            {
                                "number": issue.get("number"),
                                "title": issue.get("title", ""),
                                "category": issue.get("category", ""),
                                "product_area": issue.get("product_area", ""),
                            }
                        )

        return extracted

    # ------------------------------------------------------------------
    # Apollo enrichment
    # ------------------------------------------------------------------

    async def enrich_competitor_profile(
        self,
        name: str,
        domain: str,
    ) -> dict[str, Any] | None:
        """Enrich a competitor with Apollo org data."""
        if not self.apollo_client:
            return None
        try:
            org = await self.apollo_client.enrich_organization(domain=domain)
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

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a competitive intelligence task.

        Steps:
        1. Discover competitors from task string and KB
        2. Web-search each competitor for recent activity
        3. Search KB for competitive context
        4. Build LLM prompt with all gathered data
        5. Generate structured competitive report via LLM

        Degrades gracefully:
        - Without search_tools: skips web search
        - Without llm_client: returns prompt_used instead of content
        """
        logger.info(f"Rex executing: {task[:80]}...")

        # 1. Discover competitors
        competitors = self._discover_competitors(task)
        logger.info(f"Discovered competitors: {competitors}")

        # 2. Web search per competitor (semaphore-bounded parallel fan-out)
        web_intel: dict[str, list[dict[str, str]]] = {}
        if self.search_tools and competitors:
            search_sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

            async def _search_competitor(comp: str) -> tuple[str, list[dict[str, str]]]:
                async with search_sem:
                    try:
                        results = await self.search_tools.web_search(
                            f"{comp} vs {self.product_name}",
                            limit=5,
                        )
                        return comp, [
                            {"title": r.title, "url": r.url, "snippet": r.snippet} for r in results
                        ]
                    except Exception as exc:
                        logger.warning(f"Web search failed for {comp}: {exc}")
                        return comp, []

            search_results = await asyncio.gather(*[_search_competitor(c) for c in competitors])
            web_intel = dict(search_results)

        # 2b. Apollo enrichment per competitor (semaphore-bounded parallel)
        enriched_profiles: list[dict[str, Any]] = []
        if self.apollo_client and competitors:
            enrich_sem = asyncio.Semaphore(SEARCH_CONCURRENCY)

            async def _enrich(comp: str) -> dict[str, Any] | None:
                async with enrich_sem:
                    return await self.enrich_competitor_profile(comp, _guess_domain(comp))

            enrichment_results = await asyncio.gather(*[_enrich(c) for c in competitors])
            enriched_profiles = [p for p in enrichment_results if p]

        # 3. Search KB for competitive context
        kb_docs = self._kb.search(task)
        kb_context = "\n\n".join(f"[Source: {doc['source']}]\n{doc['content']}" for doc in kb_docs)

        # 4. Extract upstream context
        upstream = self._extract_upstream_context(context)

        # 5. Build prompt
        web_section = ""
        if web_intel:
            web_section = "## Web Intelligence\n"
            for comp, results in web_intel.items():
                web_section += f"\n### {comp}\n"
                for r in results:
                    web_section += f"- [{r['title']}]({r['url']}): {r['snippet']}\n"

        social_section = ""
        if upstream["social_mentions"]:
            social_section = "## Social Mentions (from Echo)\n"
            for m in upstream["social_mentions"]:
                social_section += (
                    f"- [{m['platform']}] {m['title']} (sentiment: {m['sentiment']})\n"
                )

        issues_section = ""
        if upstream["community_issues"]:
            issues_section = "## Community Issues (from Sage)\n"
            for issue in upstream["community_issues"]:
                issues_section += (
                    f"- #{issue['number']}: {issue['title']} [{issue.get('product_area', '')}]\n"
                )

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

        user_prompt = f"""Task: {task}

## Competitors Identified
{", ".join(competitors) if competitors else "No competitors identified yet."}

## Knowledge Base
{kb_context if kb_context else "No relevant knowledge base documents found."}

{web_section}

{social_section}

{issues_section}

{enriched_section}

## Instructions
Produce a competitive intelligence report in JSON with this structure:
{{
  "summary": "executive summary",
  "competitors": [
    {{
      "name": "...",
      "category": "direct|indirect|emerging",
      "strengths": ["..."],
      "weaknesses": ["..."],
      "recent_moves": ["..."],
      "market_position": "..."
    }}
  ],
  "threats": [
    {{
      "source": "...",
      "description": "...",
      "severity": "low|medium|high|critical",
      "timeframe": "immediate|short-term|long-term",
      "recommended_response": "..."
    }}
  ],
  "opportunities": [
    {{
      "description": "...",
      "source": "...",
      "impact": "low|medium|high",
      "effort": "low|medium|high",
      "recommended_action": "..."
    }}
  ]
}}

Ground all analysis in the evidence provided above. Do not speculate.
Return ONLY the JSON object.
"""

        system_prompt = self._system_prompt

        base_result: dict[str, Any] = {
            "agent": "rex",
            "task": task,
            "competitors_discovered": competitors,
            "web_intel_sources": {comp: len(results) for comp, results in web_intel.items()},
            "kb_sources": [doc["source"] for doc in kb_docs],
            "upstream_social_mentions": len(upstream["social_mentions"]),
            "upstream_community_issues": len(upstream["community_issues"]),
        }

        base_result["enriched_profiles"] = enriched_profiles

        if self.llm_client:
            try:
                raw = await self.llm_client.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.3,
                    max_tokens=4096,
                )
                cleaned = strip_markdown_fences(raw)
                try:
                    parsed = json.loads(cleaned)
                    base_result["content"] = parsed
                    base_result["status"] = "generated"
                except json.JSONDecodeError as e:
                    logger.warning(f"Rex JSON parse failed: {e}")
                    logger.debug(f"Rex raw response head: {cleaned[:500]}")
                    base_result["status"] = "parse_error"
                    base_result["raw_content"] = cleaned
                    base_result["content"] = {}
                    base_result["error"] = f"JSON parse failed: {e}"
            except Exception as exc:
                logger.warning(f"LLM generation failed: {exc}")
                base_result["status"] = "error"
                base_result["error"] = str(exc)
                base_result["prompt_used"] = user_prompt[:500]
        else:
            base_result["status"] = "generated"
            base_result["prompt_used"] = user_prompt[:500]

        return base_result
