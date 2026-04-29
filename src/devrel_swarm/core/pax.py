"""
Pax -- Sales Enablement Agent

On-demand sales asset generation: outreach emails, battle cards,
nurture sequences, objection handling docs, and one-pagers.
"""

import csv
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from devrel_swarm.core.base import get_kb_search
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.instantly_client import InstantlyClient, InstantlyLead

if TYPE_CHECKING:
    from devrel_swarm.tools.apollo_client import ApolloClient, ApolloContact
    from devrel_swarm.tools.search_tools import SearchTools

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


@dataclass
class PersonalizedOutreach:
    """A fully personalized outreach email for a specific lead."""

    contact_id: str
    first_name: str
    last_name: str
    email: str
    title: str
    company_name: str
    research_hook: str
    research_source: str
    subject: str
    body: str
    pain_points_addressed: list[str]
    sales_psychology: str


class Pax:
    """
    Sales Enablement agent for on-demand asset generation.

    Capabilities:
    - Outreach emails personalized with community pain points
    - Battle cards grounded in Rex's competitive intelligence
    - Nurture sequences for different audience segments
    - One-pagers and objection handling docs
    """

    _DEFAULT_SYSTEM_PROMPT = """You are Pax, a sales enablement specialist for {product_name}. \
Your role is to produce sales assets that help close deals: outreach emails, battle cards, \
nurture sequences, one-pagers, and objection handling docs.

Core Guidelines:
1. EVIDENCE-BASED -- Ground every claim in knowledge base facts, competitive \
data, or real community pain points. No empty marketing speak.
2. DEVELOPER-AWARE -- The buyer is often a developer or technical leader. \
Respect their intelligence. Lead with value, not hype.
3. PERSONALIZED -- Use upstream pain points and competitive gaps to make \
outreach specific and relevant to the recipient's situation.
4. ACTIONABLE -- Every asset should have a clear CTA and next step.
5. HONEST -- Never misrepresent capabilities. Acknowledge limitations when \
they exist -- credibility matters more than closing one deal.

Copywriting Psychology:
6. SELL THE MOTIVE, NOT THE NEED -- People don't buy a tool (Need); they buy \
the ability to stop worrying, to look smart in front of their boss, to sleep \
at night (Motive). Lead with the emotional payoff, then back it with evidence.
7. SELL THE NEXT STEP -- Every asset sells exactly one next step. A cold email \
sells a 10-minute demo call, not a contract. A battle card sells internal buy-in, \
not a purchase order. Match the CTA to the funnel stage.
8. FRICTIONLESS READING -- Short paragraphs (max 5 lines). Hard data over \
adjectives ("$500K team cost to $500/month" not "revolutionary savings"). \
Never end with an open question -- end with a direct CTA.
9. STORYTELLING -- Use the Fairytale Framework when appropriate: "Once upon a \
time..." (old way/pain) -> "And then one day..." (discovery) -> "And now..." \
(dream outcome). Stories bypass critical thinking and build trust.

Hormozi Offer Strategy:
10. VALUE EQUATION -- Frame every offer using: Value = (Dream Outcome x \
Perceived Likelihood) / (Time Delay x Effort). Maximize the top, drive the \
bottom to zero. Show high outcome, high likelihood, instant results, zero effort.
11. RISK REVERSAL -- Include guarantees when possible ("Run it on your repo \
for free first", "Don't pay until we generate 10 qualified leads"). Risk \
reversal is the single biggest conversion driver.
12. PREMIUM POSITIONING -- Never compete on price. Position as "replace a \
$500K-$1M team" not "cheap alternative". High prices attract better clients.
13. GIVE INFO, SELL IMPLEMENTATION -- In lead magnets and content-adjacent \
assets, give away the strategy openly. Sell the done-for-you execution."""

    @property
    def SYSTEM_PROMPT(self) -> str:
        return self._load_prompt("system_prompt.txt", self._DEFAULT_SYSTEM_PROMPT)

    # Order matters: more specific types must come before generic ones.
    # "triage_replies" must precede "outreach" because tasks containing
    # "email replies" would otherwise match "email" → outreach.
    ASSET_KEYWORDS: dict[str, list[str]] = {
        "triage_replies": ["triage", "replies", "follow-up"],
        "lead_upload": ["upload leads", "import leads", "add leads"],
        "prospect_personalize": [
            "leads and personalize",
            "prospect and personalize",
            "personalized outreach",
        ],
        "prospect_leads": ["find leads", "apollo search", "icp", "prospect leads"],
        "enrich_upload": ["enrich", "enrich and upload", "apollo enrich"],
        "instantly_campaign": ["instantly", "cold email", "outreach campaign"],
        "nurture": ["nurture", "drip", "sequence"],
        "battle_card": ["battle card", "vs", "comparison"],
        "outreach": ["outreach", "email", "prospect"],
        "one_pager": ["one-pager", "one pager", "summary"],
        "objection": ["objection", "faq", "pushback"],
    }

    FOLLOWUP_CATEGORIES = {"interested", "objection"}

    TRIAGE_PROMPT = """Classify this email reply into one of: \
interested, objection, not_now, unsubscribe, auto_reply.

Reply text:
---
{reply_body}
---

Return a JSON object: {{"category": "..."}}"""

    FOLLOWUP_PROMPT = """Draft a follow-up email for this reply.

Category: {category}
Original reply: {reply_body}
Lead: {lead_email}

## Knowledge Base Context
{kb_context}

Write a personalized, non-salesy follow-up. Be helpful and specific.
Return JSON: {{"subject": "...", "body": "..."}}"""

    # Load optimized prompts from files if available, otherwise use inline defaults
    _OPTIMIZE_DIR = Path(__file__).parent.parent / "optimize"

    @classmethod
    def _load_prompt(cls, filename: str, default: str) -> str:
        """Load prompt from optimize/ dir if it exists, else return default."""
        path = cls._OPTIMIZE_DIR / filename
        if path.exists():
            return path.read_text()
        return default

    _DEFAULT_EMAIL_PROMPT = """Write a personalized cold email for this prospect.

## Prospect
- Name: {first_name} {last_name}
- Title: {title}
- Company: {company_name}
- Research hook: {research_hook}

## Knowledge Base
{kb_context}

## Competitive Context
{competitive_context}

## Instructions
- Open with the research hook (reference something specific about them or their company)
- Connect their likely pain point to {product_name}'s solution
- Apply the Value Equation: show dream outcome, prove likelihood, emphasize speed, minimize effort
- Include risk reversal (free trial, no commitment, etc.)
- One clear CTA: book a call via {{sales_cta_url}}
- Keep it under 150 words
- No buzzwords, no "I hope this email finds you well"
- Sound like a technical peer, not a salesperson
- Sign the email as the product owner, never as "Pax" or any agent name

Return JSON:
{{"subject": "...", "body": "...", "pain_points_addressed": ["..."], "sales_psychology": "which framework you applied"}}"""

    @property
    def PERSONALIZED_EMAIL_PROMPT(self) -> str:
        return self._load_prompt("email_prompt.txt", self._DEFAULT_EMAIL_PROMPT)

    def __init__(
        self,
        api_client: PostHogClient,
        knowledge_base_path: Path,
        llm_client: Optional[LLMClient] = None,
        instantly_client: Optional[InstantlyClient] = None,
        apollo_client: Optional["ApolloClient"] = None,
        search_tools: Optional["SearchTools"] = None,
        product_name: str = "the target product",
    ):
        self.api_client = api_client
        self.knowledge_base_path = knowledge_base_path
        self.llm_client = llm_client
        self.instantly_client = instantly_client
        self.apollo_client = apollo_client
        self.search_tools = search_tools
        self.product_name = product_name
        self.sales_cta_url = os.getenv("SALES_CTA_URL", "https://example.com/book")
        self.BULK_BATCH_SIZE = 1000
        self._kb = get_kb_search(
            knowledge_base_path,
            extra_stop_words=frozenset({
                "generate", "create", "write", "outreach", "emails", "battle",
                "card", "nurture", "sequence", "one-pager",
            }),
        )

    def _collect_leads(
        self,
        leads: list[dict] | None = None,
        csv_path: Path | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[InstantlyLead]:
        """Parse leads from dicts, CSV, and/or upstream context."""
        parsed: list[InstantlyLead] = []

        if leads:
            for lead in leads:
                parsed.append(InstantlyLead(
                    email=lead.get("email", ""),
                    first_name=lead.get("first_name", ""),
                    last_name=lead.get("last_name", ""),
                    company_name=lead.get("company_name", ""),
                    title=lead.get("title", ""),
                    custom_variables=lead.get("custom_variables", {}),
                ))

        if csv_path and csv_path.exists():
            with open(csv_path) as f:
                for row in csv.DictReader(f):
                    parsed.append(InstantlyLead(
                        email=row.get("email", ""),
                        first_name=row.get("first_name", ""),
                        last_name=row.get("last_name", ""),
                        company_name=row.get("company_name", ""),
                        title=row.get("title", ""),
                    ))

        if context and "sage_triage" in context:
            for issue in context["sage_triage"].get("issues", []):
                email = issue.get("author_email")
                if email:
                    parsed.append(InstantlyLead(
                        email=email,
                        first_name=issue.get("author", ""),
                    ))

        return [lead for lead in parsed if lead.email]

    async def upload_leads(
        self,
        campaign_id: str,
        leads: list[dict] | None = None,
        csv_path: Path | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload leads to Instantly from dicts, CSV, or upstream context."""
        parsed_leads = self._collect_leads(leads, csv_path, context)

        if not parsed_leads or not self.instantly_client:
            return {"total_uploaded": 0, "batches": 0, "errors": []}

        total_uploaded = 0
        errors: list[str] = []
        batches = 0
        for i in range(0, len(parsed_leads), self.BULK_BATCH_SIZE):
            batch = parsed_leads[i : i + self.BULK_BATCH_SIZE]
            try:
                result = await self.instantly_client.add_leads_bulk(campaign_id, batch)
                total_uploaded += result.get("added", len(batch))
                batches += 1
            except Exception as e:
                errors.append(str(e))
                logger.warning(f"Bulk upload batch {batches} failed: {e}")

        return {"total_uploaded": total_uploaded, "batches": batches, "errors": errors}

    async def prospect_leads(
        self, criteria: dict,
    ) -> list["ApolloContact"]:
        """Search Apollo for contacts matching ICP criteria.

        criteria keys: titles, domains, industries, min_headcount, max_headcount
        Returns list of ApolloContact. Returns [] if no apollo_client.
        """
        if not self.apollo_client:
            return []
        # Build search params, converting headcount to Apollo's format
        # Note: skip 'industries' — Apollo requires specific tag IDs, not text
        search_params: dict[str, Any] = {}
        for key in ("titles", "domains"):
            if key in criteria:
                search_params[key] = criteria[key]
        min_hc = criteria.get("min_headcount")
        max_hc = criteria.get("max_headcount")
        if min_hc is not None or max_hc is not None:
            search_params["organization_num_employees_ranges"] = [
                f"{min_hc or 1},{max_hc or 100000}"
            ]
        result = await self.apollo_client.search_people(**search_params)
        return result.contacts

    async def enrich_and_upload(
        self,
        contacts: list["ApolloContact"],
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        """Enrich Apollo contacts and upload to Instantly.

        For contacts missing email, attempts Apollo person enrichment first.
        Then converts ApolloContact -> InstantlyLead, filters out those
        still without email, and uploads in batches.
        """
        contacts = list(contacts)  # mutable copy
        total_found = len(contacts)

        # Attempt enrichment for contacts missing email
        enriched_count = 0
        for i, contact in enumerate(contacts):
            if not contact.email and self.apollo_client and contact.linkedin_url:
                try:
                    enriched = await self.apollo_client.enrich_person(
                        linkedin_url=contact.linkedin_url,
                    )
                    if enriched and enriched.email:
                        contacts[i] = enriched
                        enriched_count += 1
                except Exception as exc:
                    logger.debug(
                        "Enrichment failed for contact %s: %s", contact.id, exc,
                    )

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
            "enriched": enriched_count,
            "uploaded": uploaded,
            "skipped_no_email": skipped,
            "errors": errors,
        }

    async def draft_followups(
        self,
        replies: list[dict],
        context: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Draft follow-up emails for interested/objection replies."""
        from devrel_swarm.core.base import strip_markdown_fences

        drafts: list[dict] = []
        actionable = [r for r in replies if r.get("category") in self.FOLLOWUP_CATEGORIES]

        if not actionable or not self.llm_client:
            return drafts

        kb_context = self._kb.search_as_text("outreach follow-up")

        for reply in actionable:
            try:
                raw = await self.llm_client.generate(
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=self.FOLLOWUP_PROMPT.format(
                        category=reply["category"],
                        reply_body=reply.get("body", "")[:1000],
                        lead_email=reply.get("lead_email", ""),
                        kb_context=kb_context,
                    ),
                    temperature=0.5,
                )
                data = json.loads(strip_markdown_fences(raw))
                drafts.append({
                    "reply_id": reply.get("reply_id"),
                    "email_id": reply.get("email_id"),
                    "draft_subject": data.get("subject", ""),
                    "draft_body": data.get("body", ""),
                    "category": reply["category"],
                    "status": "pending_approval",
                })
            except Exception as e:
                logger.warning(
                    f"Failed to draft follow-up for {reply.get('reply_id')}: {e}",
                )

        return drafts

    def _parse_asset_type(self, task: str) -> str:
        """Determine asset type from task string via keyword matching."""
        task_lower = task.lower()
        for asset_type, keywords in self.ASSET_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                return asset_type
        return "general"

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

    async def _research_prospect(
        self,
        contact: "ApolloContact",
    ) -> tuple[str, str]:
        """Research a prospect via web search. Returns (hook, source_url)."""
        if not self.search_tools:
            return ("", "")

        query = f"{contact.first_name} {contact.last_name} {contact.company_name or ''}"
        results = await self.search_tools.web_search(query, limit=3)

        if not results:
            if contact.company_name:
                results = await self.search_tools.web_search(
                    f"{contact.company_name} news announcement", limit=3,
                )

        if not results:
            return ("", "")

        content = await self.search_tools.fetch_url_content(
            results[0].url, max_chars=2000,
        )

        if not content or not self.llm_client:
            return (results[0].snippet, results[0].url)

        raw = await self.llm_client.generate(
            system_prompt="Extract a personalization hook for a cold email.",
            user_prompt=(
                f"Person: {contact.first_name} {contact.last_name}, "
                f"{contact.title} at {contact.company_name}\n\n"
                f"Web content:\n{content[:1500]}\n\n"
                "Extract one specific, relevant fact about this person or their "
                "company that could be used as an opening line in a cold email. "
                "Return ONLY the hook sentence, nothing else. If nothing relevant "
                "is found, return 'NO_HOOK'."
            ),
            temperature=0.3,
            max_tokens=200,
        )

        hook = raw.strip()
        if hook == "NO_HOOK":
            return ("", "")

        return (hook, results[0].url)

    async def _generate_personalized_email(
        self,
        contact: "ApolloContact",
        research_hook: str,
        kb_context: str,
        competitive_context: str,
    ) -> dict[str, Any] | None:
        """Generate a personalized email for one contact. Returns parsed JSON or None."""
        from devrel_swarm.core.base import strip_markdown_fences

        if not self.llm_client:
            return None

        prompt = self.PERSONALIZED_EMAIL_PROMPT.format(
            first_name=contact.first_name,
            last_name=contact.last_name,
            title=contact.title or "Unknown",
            company_name=contact.company_name or "Unknown",
            research_hook=research_hook or "No specific hook found — use title and company context.",
            kb_context=kb_context,
            competitive_context=competitive_context,
            product_name=self.product_name,
            sales_cta_url=self.sales_cta_url,
        )

        try:
            raw = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT.format(product_name=self.product_name),
                user_prompt=prompt,
                temperature=0.5,
                max_tokens=1024,
            )
            return json.loads(strip_markdown_fences(raw))
        except Exception as exc:
            logger.warning(f"Email generation failed for {contact.email}: {exc}")
            return None

    async def _execute_prospect_personalize(
        self,
        task: str,
        asset_type: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Full prospect -> research -> personalize -> upload flow."""
        import asyncio
        from devrel_swarm.core.base import strip_markdown_fences

        # Step 1: Extract ICP criteria via LLM
        criteria: dict[str, Any] = {}
        if self.llm_client:
            try:
                raw = await self.llm_client.generate(
                    system_prompt="Extract ICP criteria from the task for an Apollo.io people search.",
                    user_prompt=(
                        f"Extract search criteria from: {task}\n"
                        "Return JSON with these optional keys:\n"
                        '- "titles": list of job titles (each title separate, no OR)\n'
                        '- "industries": list of industry tags\n'
                        '- "domains": list of company domains\n'
                        '- "min_headcount": integer\n'
                        '- "max_headcount": integer\n'
                        'Example: {{"titles": ["Head of Developer Relations", "VP Developer Experience"], '
                        '"min_headcount": 50, "max_headcount": 500}}'
                    ),
                    temperature=0.0,
                )
                criteria = json.loads(strip_markdown_fences(raw))
                normalised: dict[str, Any] = {}
                if "title" in criteria:
                    val = criteria["title"]
                    normalised["titles"] = [val] if isinstance(val, str) else val
                if "industry" in criteria:
                    val = criteria["industry"]
                    normalised["industries"] = [val] if isinstance(val, str) else val
                if "domain" in criteria:
                    val = criteria["domain"]
                    normalised["domains"] = [val] if isinstance(val, str) else val
                for key in ("titles", "industries", "domains",
                            "min_headcount", "max_headcount"):
                    if key in criteria:
                        normalised[key] = criteria[key]
                criteria = normalised
            except Exception:
                logger.warning("ICP extraction failed, using empty criteria")

        # Step 2: Apollo search
        try:
            contacts = list(await self.prospect_leads(criteria))
        except Exception as exc:
            logger.warning(f"Apollo search failed: {exc}")
            return {
                "agent": "pax", "task": task, "asset_type": asset_type,
                "status": "error", "error": str(exc), "contacts_found": 0,
            }
        if not contacts:
            return {
                "agent": "pax", "task": task, "asset_type": asset_type,
                "status": "personalized", "contacts_found": 0,
            }

        logger.info(f"Prospecting {len(contacts)} contacts for personalized outreach")

        # Step 3: Reveal/enrich contacts missing email via person ID or LinkedIn
        enriched_count = 0
        for i, contact in enumerate(contacts):
            if not contact.email and self.apollo_client:
                try:
                    enrich_kwargs: dict[str, str] = {}
                    if contact.id:
                        enrich_kwargs["person_id"] = contact.id
                    elif contact.linkedin_url:
                        enrich_kwargs["linkedin_url"] = contact.linkedin_url
                    if enrich_kwargs:
                        enriched = await self.apollo_client.enrich_person(**enrich_kwargs)
                        if enriched and enriched.email:
                            contacts[i] = enriched
                            enriched_count += 1
                except Exception as exc:
                    logger.debug(f"Enrichment failed for {contact.first_name}: {exc}")

        if enriched_count:
            logger.info(f"Revealed {enriched_count}/{len(contacts)} contact emails")
        contacts_with_email = [c for c in contacts if c.email]
        logger.info(f"{len(contacts_with_email)} contacts with email out of {len(contacts)}")

        # Prepare shared context
        kb_context = self._kb.search_as_text(task)
        upstream = self._extract_upstream_context(context)
        competitive_context = ""
        for c in upstream["competitors"][:5]:
            if isinstance(c, dict):
                competitive_context += (
                    f"- {c.get('name', '?')}: {c.get('strengths', [])}\n"
                )

        # Step 4: Research + generate per contact
        outreach_list: list[dict] = []
        hooks_found = 0
        for contact in contacts_with_email:
            logger.info(
                f"Researching {contact.first_name} {contact.last_name} "
                f"at {contact.company_name}",
            )
            hook, source_url = await self._research_prospect(contact)
            if hook:
                hooks_found += 1

            email_data = await self._generate_personalized_email(
                contact, hook, kb_context, competitive_context,
            )

            if email_data:
                outreach_list.append({
                    "contact_id": contact.id,
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "email": contact.email,
                    "title": contact.title or "",
                    "company_name": contact.company_name or "",
                    "research_hook": hook,
                    "research_source": source_url,
                    "subject": email_data.get("subject", ""),
                    "body": email_data.get("body", ""),
                    "pain_points_addressed": email_data.get(
                        "pain_points_addressed", [],
                    ),
                    "sales_psychology": email_data.get("sales_psychology", ""),
                })

            await asyncio.sleep(1.0)

        # Step 5-6: Upload to Instantly
        uploaded = 0
        campaign_id = ""
        errors: list[str] = []
        if outreach_list and self.instantly_client:
            leads = [
                InstantlyLead(
                    email=o["email"],
                    first_name=o["first_name"],
                    last_name=o["last_name"],
                    company_name=o["company_name"],
                    custom_variables={
                        "personalized_subject": o["subject"],
                        "personalized_body": o["body"],
                        "title": o["title"],
                        "research_hook": o["research_hook"],
                    },
                )
                for o in outreach_list
            ]
            campaign_id = (context or {}).get("campaign_id", "")
            # Auto-create campaign if none provided
            if not campaign_id:
                try:
                    from datetime import datetime
                    campaign_name = (
                        f"{self.product_name} Outreach "
                        f"{datetime.now().strftime('%Y-%m-%d')}"
                    )
                    # Fetch sending accounts to attach to campaign
                    sending_accounts: list[str] = []
                    try:
                        acct_data = await self.instantly_client._request(
                            "GET", "/api/v2/accounts", params={"limit": 10},
                        )
                        acct_items = acct_data.get("items", acct_data if isinstance(acct_data, list) else [])
                        sending_accounts = [a["email"] for a in acct_items if a.get("email")]
                    except Exception:
                        logger.debug("Could not fetch Instantly sending accounts")

                    campaign = await self.instantly_client.create_campaign(
                        name=campaign_name,
                        sequences=[{
                            "steps": [{
                                "type": "email",
                                "delay": 0,
                                "variants": [{
                                    "subject": "{{personalized_subject}}",
                                    "body": "{{personalized_body}}",
                                }],
                            }],
                        }],
                        accounts=sending_accounts or None,
                    )
                    campaign_id = campaign.id
                    logger.info(
                        f"Created Instantly campaign: {campaign_name} ({campaign_id})"
                    )
                except Exception as e:
                    errors.append(f"Campaign creation failed: {e}")
                    logger.warning(f"Instantly campaign creation failed: {e}")
            if campaign_id:
                try:
                    result = await self.instantly_client.add_leads_bulk(
                        campaign_id, leads,
                    )
                    uploaded = result.get("added", len(leads))
                    logger.info(f"Uploaded {uploaded} leads to Instantly campaign {campaign_id}")
                except Exception as e:
                    errors.append(str(e))
                    logger.warning(f"Instantly upload failed: {e}")

        logger.info(
            f"Personalized outreach complete: {len(outreach_list)} emails "
            f"generated, {hooks_found} hooks found",
        )

        return {
            "agent": "pax",
            "task": task,
            "asset_type": asset_type,
            "status": "personalized",
            "contacts_found": len(contacts),
            "contacts_with_email": len(contacts_with_email),
            "contacts_researched": len(contacts_with_email),
            "hooks_found": hooks_found,
            "emails_generated": len(outreach_list),
            "uploaded_to_instantly": uploaded,
            "campaign_id": campaign_id if campaign_id else None,
            "outreach": outreach_list,
            "enriched": enriched_count,
            "skipped_no_email": len(contacts) - len(contacts_with_email),
            "errors": errors,
        }

    async def _execute_campaign(
        self, task: str, asset_type: str, base_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle the instantly_campaign execute path."""
        from devrel_swarm.core.base import strip_markdown_fences

        prompt_text = (
            f"Create a cold email outreach campaign for {self.product_name}. "
            f"Return JSON: {{\"sequences\": [{{\"subject\": \"...\", "
            f"\"body\": \"...\", \"delay_days\": N}}]}}"
        )
        try:
            raw = await self.llm_client.generate(
                system_prompt=self.SYSTEM_PROMPT.format(
                    product_name=self.product_name,
                ),
                user_prompt=prompt_text,
            )
            data = json.loads(strip_markdown_fences(raw))
            sequences = data.get("sequences", [])
            campaign = await self.instantly_client.create_campaign(
                name=f"{self.product_name} - Outreach",
                sequences=sequences,
            )
            return {
                "agent": "pax", "task": task, "asset_type": asset_type,
                "status": "campaign_created",
                "campaign_id": campaign.id, "campaign_name": campaign.name,
            }
        except Exception as exc:
            logger.warning(f"Campaign creation failed: {exc}")
            base_result["prompt_used"] = prompt_text[:500]
            return base_result

    async def _classify_email(self, email: Any) -> dict:
        """Classify a single email reply using LLM."""
        from devrel_swarm.core.base import strip_markdown_fences

        base = {
            "reply_id": email.id, "email_id": email.id,
            "body": email.body, "lead_email": email.lead_email,
        }
        if not self.llm_client:
            return {**base, "category": "not_now"}
        try:
            raw = await self.llm_client.generate(
                system_prompt="You classify cold outreach email replies for sales triage.",
                user_prompt=self.TRIAGE_PROMPT.format(
                    reply_body=email.body[:1000],
                ),
                temperature=0.0,
                max_tokens=256,
                model="haiku",
            )
            data = json.loads(strip_markdown_fences(raw))
            return {**base, "category": data.get("category", "not_now")}
        except Exception:
            return {**base, "category": "not_now"}

    async def _execute_triage(
        self, task: str, asset_type: str,
        context: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Handle the triage_replies execute path."""
        emails = await self.instantly_client.list_emails(is_reply=True)
        classified = [await self._classify_email(e) for e in emails]
        drafts = await self.draft_followups(classified, context)
        categories: dict[str, int] = {}
        for c in classified:
            categories[c["category"]] = categories.get(c["category"], 0) + 1
        return {
            "agent": "pax", "task": task, "asset_type": asset_type,
            "status": "triaged", "total_replies": len(classified),
            "categories": categories, "drafts": drafts,
        }

    async def _execute_prospect(
        self, task: str, asset_type: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Handle the prospect_leads execute path."""
        criteria: dict[str, Any] = {}
        if self.llm_client:
            try:
                from devrel_swarm.core.base import strip_markdown_fences
                raw = await self.llm_client.generate(
                    system_prompt="Extract ICP criteria from the task for an Apollo.io people search.",
                    user_prompt=(
                        f"Extract search criteria from: {task}\n"
                        "Return JSON with these optional keys:\n"
                        '- "titles": list of job titles (each title separate, no OR)\n'
                        '- "industries": list of industry tags\n'
                        '- "domains": list of company domains\n'
                        '- "min_headcount": integer\n'
                        '- "max_headcount": integer\n'
                        'Example: {{"titles": ["Head of Developer Relations", "VP Developer Experience"], '
                        '"min_headcount": 50, "max_headcount": 500}}'
                    ),
                    temperature=0.0,
                )
                criteria = json.loads(strip_markdown_fences(raw))
                # Normalise singular LLM keys to plural list-based keys
                normalised: dict[str, Any] = {}
                if "title" in criteria:
                    val = criteria["title"]
                    normalised["titles"] = [val] if isinstance(val, str) else val
                if "industry" in criteria:
                    val = criteria["industry"]
                    normalised["industries"] = [val] if isinstance(val, str) else val
                if "domain" in criteria:
                    val = criteria["domain"]
                    normalised["domains"] = [val] if isinstance(val, str) else val
                # Pass through only known search_people params
                for key in ("titles", "industries", "domains",
                            "min_headcount", "max_headcount"):
                    if key in criteria:
                        normalised[key] = criteria[key]
                criteria = normalised
            except Exception:
                pass
        try:
            contacts = await self.prospect_leads(criteria)
        except Exception as exc:
            logger.warning(f"Apollo search failed: {exc}")
            return {
                "agent": "pax", "task": task, "asset_type": asset_type,
                "status": "error", "error": str(exc), "contacts_found": 0,
            }
        upload_result: dict[str, Any] = {}
        if contacts and self.instantly_client:
            upload_result = await self.enrich_and_upload(
                contacts,
                campaign_id=(context or {}).get("campaign_id"),
            )
        return {
            "agent": "pax", "task": task, "asset_type": asset_type,
            "status": "prospected",
            "contacts_found": len(contacts),
            **upload_result,
        }

    async def _execute_enrich_upload(
        self, task: str, asset_type: str,
        context: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Handle the enrich_upload execute path."""
        raw_contacts = (context or {}).get("apollo_contacts", [])
        from devrel_swarm.tools.apollo_client import ApolloContact as AC
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
        campaign_id = (context or {}).get("campaign_id")
        result = await self.enrich_and_upload(contacts, campaign_id=campaign_id)
        return {
            "agent": "pax", "task": task, "asset_type": asset_type,
            "status": "enriched_and_uploaded", **result,
        }

    def _build_asset_prompt(
        self, task: str, asset_type: str,
        upstream: dict[str, Any], kb_context: str,
    ) -> str:
        """Build the LLM prompt for generic asset generation."""
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

        return f"""Task: {task}
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
        kb_context = self._kb.search_as_text(task)
        prompt = self._build_asset_prompt(task, asset_type, upstream, kb_context)

        base_result: dict[str, Any] = {
            "agent": "pax",
            "task": task,
            "asset_type": asset_type,
            "status": "generated",
        }

        # Handle Apollo-specific asset types
        if asset_type == "prospect_personalize" and self.apollo_client:
            return await self._execute_prospect_personalize(task, asset_type, context)

        if asset_type == "prospect_leads" and self.apollo_client:
            return await self._execute_prospect(task, asset_type, context)

        if asset_type == "enrich_upload" and self.apollo_client:
            return await self._execute_enrich_upload(task, asset_type, context)

        # Handle Instantly-specific asset types
        if asset_type == "instantly_campaign" and self.instantly_client and self.llm_client:
            return await self._execute_campaign(task, asset_type, base_result)

        if asset_type == "lead_upload" and self.instantly_client:
            return {
                "agent": "pax",
                "task": task,
                "asset_type": asset_type,
                "status": "uploaded",
                **(await self.upload_leads(
                    campaign_id=context.get("campaign_id", "") if context else "",
                    context=context,
                )),
            }

        if asset_type == "triage_replies" and self.instantly_client:
            return await self._execute_triage(task, asset_type, context)

        if self.llm_client:
            try:
                raw, trace = await self.llm_client.generate_with_revision(
                    system_prompt=self.SYSTEM_PROMPT.format(
                        product_name=self.product_name,
                    ),
                    user_prompt=prompt,
                    temperature=0.5,
                    max_tokens=4096,
                    max_rounds=2,
                    min_score=7,
                    content_type="sales",
                )
                base_result["content"] = raw
                base_result["revision"] = {
                    "rounds": trace.revision_rounds,
                    "final_score": trace.final_score,
                }
            except Exception as exc:
                logger.warning(f"LLM generation failed: {exc}")
                base_result["prompt_used"] = prompt[:500]
        else:
            base_result["prompt_used"] = prompt[:500]

        return base_result
