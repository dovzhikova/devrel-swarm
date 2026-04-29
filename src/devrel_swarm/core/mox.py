"""
Mox -- Campaign Marketing Agent

On-demand marketing content and campaign generation: SEO blog posts,
landing page copy, social media batches, launch campaigns, and press releases.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from devrel_swarm.core.base import get_kb_search, load_agent_prompt, strip_markdown_fences
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.quality import generate_with_pipeline
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.code_validator import CodeValidator
from devrel_swarm.tools.instantly_client import CampaignAnalytics, InstantlyClient
from devrel_swarm.tools.search_tools import SearchTools

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

    _DEFAULT_SYSTEM_PROMPT = """You are Mox, a campaign marketing specialist for {product_name}. \
Your role is to produce marketing content and campaigns that drive awareness, engagement, \
and conversion among developers and technical decision-makers.

Core Guidelines:
1. DEVELOPER-AUTHENTIC -- Write like a developer advocate, not a marketer. \
No buzzwords, no fluff. Technical audiences smell inauthenticity instantly.
2. SEO-AWARE -- Structure blog posts with clear H2/H3 hierarchy, include \
target keywords naturally, write compelling meta descriptions.
3. PAIN-POINT-DRIVEN -- Every piece of content should address a real developer \
frustration identified by upstream agents, not invented marketing problems.
4. DIFFERENTIATED -- Use competitive intelligence to position against \
alternatives. Show don't tell -- concrete features, not vague claims.
5. MULTI-FORMAT -- Adapt messaging for each platform's conventions. Twitter \
threads != LinkedIn posts != Reddit comments.

Copywriting Psychology:
6. SELL THE MOTIVE, NOT THE NEED -- People don't buy tools (Need); they buy \
peace of mind, competitive advantage, time back (Motive). Lead with the \
emotional payoff, then back it with technical evidence.
7. ONE NEXT STEP PER ASSET -- Every piece of content sells exactly one next \
step. A blog post sells a demo booking. A tweet sells a link click. A LinkedIn \
post sells a profile visit. Never try to close from content.
8. FRICTIONLESS READING -- Max 5 lines per paragraph (mobile-first). Replace \
subjective adjectives with hard data ("reduced triage from 12hrs to 30min" \
not "dramatically improved"). Never end with an open question -- end with a \
firm conclusion or direct CTA.
9. STORYTELLING -- Use the Fairytale Framework: "Once upon a time..." (old \
way/pain) -> "And then one day..." (discovery) -> "And now..." (dream \
outcome). Stories bypass critical thinking and build instant trust.
10. POST ARCHITECTURE -- Headlines use numbers, How-To, or extreme pain \
points. Body is hard facts, short paragraphs. Kicker is a direct command, \
never an open question.

Hormozi Offer Strategy:
11. LEAD MAGNET CONTENT -- Give away the information (architecture, workflow, \
strategy) openly and generously. Free content should be better than \
competitors' paid stuff. Sell the implementation (managed deployment, custom \
setup, done-for-you execution).
12. VALUE EQUATION IN COPY -- Frame benefits using: Value = (Dream Outcome x \
Perceived Likelihood) / (Time Delay x Effort). Show the dream outcome \
vividly, prove likelihood with data, emphasize speed and zero-effort.
13. PREMIUM POSITIONING -- Never position on price. Frame as "replace a \
$500K-$1M team" not "affordable tool". Cost comparison is a proof point, \
not a selling point.
14. SCARCITY & URGENCY -- When appropriate, use genuine constraints to drive \
action ("Only onboarding 4 companies this month", "Beta spots limited"). \
Never fake scarcity."""

    @property
    def SYSTEM_PROMPT(self) -> str:
        return load_agent_prompt("mox", "system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT)

    CONTENT_KEYWORDS: dict[str, list[str]] = {
        "blog": ["blog", "seo", "article"],
        "landing_page": ["landing page", "landing copy"],
        "social": ["social", "twitter", "linkedin", "reddit"],
        "email_campaign": ["email campaign", "cold email", "drip campaign"],
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
        instantly_client: Optional[InstantlyClient] = None,
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.search_tools = search_tools
        self.instantly_client = instantly_client
        self.product_name = product_name
        self.code_validator = CodeValidator()
        self._kb = get_kb_search(
            knowledge_base_path,
            extra_stop_words=frozenset({
                "write", "generate", "create", "blog", "post", "landing", "page",
                "social", "media", "posts", "campaign", "press", "release",
            }),
        )

    def _parse_content_type(self, task: str) -> str:
        """Determine content type from task string via keyword matching."""
        task_lower = task.lower()
        for content_type, keywords in self.CONTENT_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                return content_type
        return "blog"  # default

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
        empty = {
            "total_campaigns": 0, "total_sent": 0, "total_opened": 0,
            "total_replied": 0, "total_bounced": 0, "avg_open_rate": 0.0,
            "avg_reply_rate": 0.0, "avg_bounce_rate": 0.0, "per_campaign": [],
        }
        if not self.instantly_client:
            return empty

        if campaign_ids:
            ids = campaign_ids
        else:
            campaigns = await self.instantly_client.list_campaigns()
            ids = [c.id for c in campaigns if c.status == "active"]

        async def _fetch_analytics(cid: str) -> CampaignAnalytics | None:
            try:
                return await self.instantly_client.get_campaign_analytics(cid)
            except Exception as e:
                logger.warning(f"Failed to get analytics for {cid}: {e}")
                return None

        results = await asyncio.gather(*[_fetch_analytics(cid) for cid in ids])
        analytics: list[CampaignAnalytics] = [a for a in results if a is not None]

        if not analytics:
            return empty

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
                {
                    "campaign_id": a.campaign_id,
                    "campaign_name": a.campaign_name,
                    "emails_sent": a.emails_sent,
                    "reply_rate": a.reply_rate,
                }
                for a in analytics
            ],
        }

    def _build_content_prompt(
        self,
        task: str,
        content_type: str,
        upstream: dict[str, Any],
        kb_context: str,
    ) -> str:
        """Build the LLM prompt for content generation."""
        competitive_section = ""
        if upstream["competitors"]:
            competitive_section = "Competitive landscape:\n"
            for c in upstream["competitors"][:5]:
                if isinstance(c, dict):
                    competitive_section += (
                        f"- {c.get('name', '?')}: "
                        f"strengths={c.get('strengths', [])}\n"
                    )

        pain_section = ""
        if upstream["pain_points"]:
            pain_section = "Developer pain points to address:\n"
            for pp in upstream["pain_points"][:5]:
                if isinstance(pp, dict):
                    pain_section += (
                        f"- {pp.get('title', '?')} "
                        f"(severity: {pp.get('severity', '?')})\n"
                    )

        existing_section = ""
        if upstream["existing_content"]:
            existing_section = (
                f"Existing tutorial content (for reference/repurposing):\n"
                f"{upstream['existing_content'][:1000]}"
            )

        return f"""Task: {task}
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

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute a marketing content generation task."""
        logger.info(f"Mox executing: {task[:80]}...")

        content_type = self._parse_content_type(task)
        upstream = self._extract_upstream_context(context)
        kb_context = self._kb.search_as_text(task)
        prompt = self._build_content_prompt(task, content_type, upstream, kb_context)

        # Handle email_campaign type with Instantly push
        if content_type == "email_campaign" and self.instantly_client and self.llm_client:
            email_prompt = f"""{prompt}

## Output Format
Return ONLY a JSON object with this structure:
{{
  "sequences": [
    {{"subject": "...", "body": "...", "delay_days": N}}
  ]
}}

Use pain points and competitive positioning from above to craft the sequence.
Each email should sell one next step. 3-5 emails in the sequence."""
            try:
                raw = await self.llm_client.generate(
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=email_prompt,
                    temperature=0.5,
                )
                data = json.loads(strip_markdown_fences(raw))
                sequences = data.get("sequences", [])
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
            except Exception as exc:
                logger.warning(f"Email campaign creation failed: {exc}")
                # Fall through to normal generation

        base_result: dict[str, Any] = {
            "agent": "mox",
            "task": task,
            "content_type": content_type,
            "status": "generated",
        }

        if self.llm_client:
            try:
                # Map Mox's parsed content_type to the pipeline's vocabulary.
                # blog_post / landing_page exist verbatim in DEFAULT_TARGETS;
                # social falls back to blog_post readability targets.
                pipeline_content_type = {
                    "blog": "blog_post",
                    "landing_page": "landing_page",
                    "social": "blog_post",
                }.get(content_type, "blog_post")
                raw, strengths, issues = await generate_with_pipeline(
                    llm_client=self.llm_client,
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=prompt,
                    content_type=pipeline_content_type,
                    logger=logger,
                )
                base_result["content"] = raw
                base_result["revision"] = {
                    "strengths": strengths,
                    "issues": issues,
                }

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
