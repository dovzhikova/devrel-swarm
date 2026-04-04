# Lead Search + Personalized Outreach — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Author:** Claude (brainstorming session with Daria)

---

## Problem

The current pipeline can search Apollo for leads and generate outreach emails, but these are separate, disconnected capabilities:

1. `Pax._execute_prospect()` searches Apollo and uploads leads to Instantly, but generates **no personalized email content** per lead.
2. `Pax.execute()` generates outreach emails from task descriptions, but uses **zero lead-specific data** (no title, company, industry, or research hooks).
3. There is **no web research step** to find personalization hooks (recent company news, blog posts, product launches).

The result: generic emails that ignore who the prospect actually is.

## Goal

Add a single new Pax method — `prospect_and_personalize()` — that chains the full lead-to-outreach flow: Apollo search -> enrichment -> web research per lead -> per-lead LLM email generation -> Instantly upload. Triggered by one task string in `run_sales_pipeline.py`.

## Constraints

- **10-20 leads per run** — deep personalization, not volume plays
- **Per-lead LLM calls** — each prospect gets a fully custom email body using their Apollo data + a web research hook
- **Web research via existing `SearchTools`** — Firecrawl/Brave already wired in, no new dependencies
- **Copywriting/Hormozi frameworks** apply — system prompt already has them
- **Results flow through SharedContext** so downstream agents (Mox) can reference the leads

## Non-Goals

- Structured ICP definition files (keeping LLM-from-task extraction for now)
- Multi-step drip sequences per lead (just one personalized first-touch email)
- Lead scoring or qualification logic
- CRM integration beyond Instantly

---

## Design

### New Dataclass: `PersonalizedOutreach`

```python
# In agents/pax.py

@dataclass
class PersonalizedOutreach:
    """A fully personalized outreach email for a specific lead."""

    contact_id: str
    first_name: str
    last_name: str
    email: str
    title: str
    company_name: str
    research_hook: str          # The personalization hook found via web research
    research_source: str        # URL where the hook was found
    subject: str                # Personalized email subject
    body: str                   # Personalized email body
    pain_points_addressed: list[str]
    sales_psychology: str       # Which framework was applied (Value Equation, Risk Reversal, etc.)
```

### New Keyword Trigger

Add to `ASSET_KEYWORDS` **before** `"prospect_leads"`:

```python
"prospect_personalize": ["find leads and personalize", "prospect and personalize", "personalized outreach"],
```

Keyword matching uses substring check (`kw in task_lower`), so ordering matters. Since `prospect_personalize` is checked before `prospect_leads`, a task containing "find leads and personalize" matches the longer phrase first. A task with just "find leads" (no "personalize") correctly falls through to `prospect_leads`.

**Keyword routing test matrix** (add to tests):

| Task string | Expected asset_type |
|---|---|
| "Find 15 leads and personalize outreach for DevRel leaders" | `prospect_personalize` |
| "Prospect and personalize emails for VP Engineering" | `prospect_personalize` |
| "Send personalized outreach to DevTools founders" | `prospect_personalize` |
| "Find leads matching our ICP at Series B companies" | `prospect_leads` |
| "Write an outreach email to a Head of DevRel" | `outreach` |

### New Method: `_execute_prospect_personalize()`

Called from `execute()` when `asset_type == "prospect_personalize"`.

### `execute()` Dispatch Change

Add this block in `execute()` **before** the existing `prospect_leads` check (line 641):

```python
if asset_type == "prospect_personalize" and self.apollo_client:
    return await self._execute_prospect_personalize(task, asset_type, context)
```

**Flow:**

```
Task string
    │
    ▼
1. LLM extracts ICP criteria (existing _execute_prospect pattern)
    │
    ▼
2. apollo_client.search_people(**criteria, per_page=20)
    │
    ▼
3. Enrich contacts missing email (existing enrich_and_upload pattern)
    │
    ▼
4. For each contact with email:
    │
    ├─ 4a. Web research: SearchTools.web_search("{first_name} {last_name} {company_name}")
    │      → Take top 1-2 results
    │      → SearchTools.fetch_url_content(result.url, max_chars=2000)
    │      → Extract a 1-2 sentence personalization hook via LLM
    │
    ├─ 4b. LLM generates personalized email using:
    │      - Contact data (title, company, industry)
    │      - Research hook from 4a
    │      - KB context (product knowledge)
    │      - Upstream competitive intel from Rex (via context)
    │      - Copywriting/Hormozi system prompt guidelines
    │
    └─ 4c. Build PersonalizedOutreach object
    │
    ▼
5. Convert contacts to InstantlyLead with custom_variables containing
   personalized subject + body
    │
    ▼
6. Upload to Instantly (existing add_leads_bulk pattern)
    │
    ▼
7. Return result dict with all PersonalizedOutreach objects + stats
```

### Import Pattern

Follow existing `TYPE_CHECKING` pattern in `pax.py`:

```python
if TYPE_CHECKING:
    from tools.apollo_client import ApolloClient, ApolloContact
    from tools.search_tools import SearchTools
```

Use string annotations (`"ApolloContact"`, `"SearchTools"`) in method signatures at runtime.

### Web Research Step (4a) — Detail

```python
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
        # Fallback: search by company only
        if contact.company_name:
            results = await self.search_tools.web_search(
                f"{contact.company_name} news announcement", limit=3
            )

    if not results:
        return ("", "")

    # Fetch content from top result
    content = await self.search_tools.fetch_url_content(
        results[0].url, max_chars=2000
    )

    if not content or not self.llm_client:
        return (results[0].snippet, results[0].url)

    # LLM extracts a concise personalization hook
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
```

### Per-Lead Email Generation (4b) — Detail

```python
PERSONALIZED_EMAIL_PROMPT = """Write a personalized cold email for this prospect.

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
- One clear CTA: book a 15-minute demo call
- Keep it under 150 words
- No buzzwords, no "I hope this email finds you well"
- Sound like a technical peer, not a salesperson

Return JSON:
{{
    "subject": "...",
    "body": "...",
    "pain_points_addressed": ["..."],
    "sales_psychology": "which framework you applied"
}}"""
```

### Per-Lead Email Generation Method (4b) — Detail

```python
async def _generate_personalized_email(
    self,
    contact: "ApolloContact",
    research_hook: str,
    kb_context: str,
    competitive_context: str,
) -> dict[str, Any] | None:
    """Generate a personalized email for a single contact. Returns parsed JSON or None."""
    from agents.base import strip_markdown_fences

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
```

**Context preparation:**
- `kb_context` comes from `self._kb.search_as_text(task)` (same pattern as existing `execute()`)
- `competitive_context` is built from `self._extract_upstream_context(context)["competitors"]` using the same formatting as `_build_asset_prompt()`

### Full Orchestration Method — `_execute_prospect_personalize()`

```python
async def _execute_prospect_personalize(
    self,
    task: str,
    asset_type: str,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Full prospect → research → personalize → upload flow."""
    import asyncio
    from agents.base import strip_markdown_fences

    # Step 1: Extract ICP criteria via LLM (same as _execute_prospect)
    criteria = {}
    if self.llm_client:
        try:
            raw = await self.llm_client.generate(
                system_prompt="Extract ICP criteria from the task.",
                user_prompt=f"Extract search criteria from: {task}\nReturn JSON: ...",
                temperature=0.0,
            )
            criteria = json.loads(strip_markdown_fences(raw))
            # Normalise keys (same as _execute_prospect)
            ...
        except Exception:
            pass

    # Step 2: Apollo search
    contacts = await self.prospect_leads(criteria)
    if not contacts:
        return {"agent": "pax", "task": task, "asset_type": asset_type,
                "status": "personalized", "contacts_found": 0}

    # Step 3: Enrich contacts missing email
    for i, contact in enumerate(contacts):
        if not contact.email and self.apollo_client and contact.linkedin_url:
            try:
                enriched = await self.apollo_client.enrich_person(
                    linkedin_url=contact.linkedin_url)
                if enriched and enriched.email:
                    contacts[i] = enriched
            except Exception as exc:
                logger.debug(f"Enrichment failed: {exc}")

    # Filter to contacts with email
    contacts_with_email = [c for c in contacts if c.email]

    # Prepare shared context for email generation
    kb_context = self._kb.search_as_text(task)
    upstream = self._extract_upstream_context(context)
    competitive_context = ""
    for c in upstream["competitors"][:5]:
        if isinstance(c, dict):
            competitive_context += f"- {c.get('name', '?')}: {c.get('strengths', [])}\n"

    # Step 4: Research + generate per contact
    outreach_list: list[dict] = []
    hooks_found = 0
    for contact in contacts_with_email:
        # 4a: Web research
        hook, source_url = await self._research_prospect(contact)
        if hook:
            hooks_found += 1

        # 4b: Generate personalized email
        email_data = await self._generate_personalized_email(
            contact, hook, kb_context, competitive_context)

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
                "pain_points_addressed": email_data.get("pain_points_addressed", []),
                "sales_psychology": email_data.get("sales_psychology", ""),
            })

        # Rate limit: 1s between research calls
        await asyncio.sleep(1.0)

    # Step 5-6: Upload to Instantly
    uploaded = 0
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
        try:
            result = await self.instantly_client.add_leads_bulk("", leads)
            uploaded = result.get("added", len(leads))
        except Exception as e:
            errors.append(str(e))

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
        "outreach": outreach_list,
        "skipped_no_email": len(contacts) - len(contacts_with_email),
        "errors": errors,
    }
```

**Instantly custom_variables note:** The keys `personalized_subject` and `personalized_body` must match the variable names used in the Instantly campaign email template (e.g., `{{personalized_subject}}` and `{{personalized_body}}`). Configure the Instantly campaign template accordingly.

### Logging

All new methods should follow the existing `logger.info` / `logger.warning` pattern:
- `logger.info(f"Prospecting {len(contacts)} contacts for personalized outreach")`
- `logger.info(f"Researching {contact.first_name} {contact.last_name} at {contact.company_name}")`
- `logger.warning(f"Research failed for {contact.email}: {exc}")`
- `logger.info(f"Personalized outreach complete: {len(outreach_list)} emails generated, {hooks_found} hooks found")`

### Pax Constructor Change

`SearchTools` is currently only on Mox, not Pax. Pax needs it for web research.

```python
def __init__(
    self,
    api_client: PostHogClient,
    knowledge_base_path: Path,
    llm_client: Optional[LLMClient] = None,
    instantly_client: Optional[InstantlyClient] = None,
    apollo_client: Optional["ApolloClient"] = None,
    search_tools: Optional["SearchTools"] = None,   # NEW
    product_name: str = "the target product",
):
    ...
    self.search_tools = search_tools   # NEW
```

### `run_sales_pipeline.py` Changes

1. Pass `search_tools` to Pax constructor.
2. Add a new pipeline stage between Rex and the existing Pax stages:

```python
# ── Stage 2a: Pax — Prospect + Personalize ─────────────────
pax_personalized = await pax.execute(
    task=(
        "Find 15 leads and personalize outreach: Head of Developer Relations "
        "or VP Developer Experience at Series B-C DevTools companies with "
        "50-500 employees. Position OpenClaw as the force multiplier "
        "that replaces a 10-person team with 10 specialized AI agents."
    ),
    context=context,
)
results["pax_personalized"] = pax_personalized
context["pax_personalized"] = pax_personalized
```

### SharedContext Integration

The result dict from `prospect_and_personalize()` is stored in `context["pax_personalized"]` so Mox can reference it. The result structure:

```python
{
    "agent": "pax",
    "task": "...",
    "asset_type": "prospect_personalize",
    "status": "personalized",
    "contacts_found": 15,
    "contacts_with_email": 12,
    "contacts_researched": 12,
    "hooks_found": 9,
    "emails_generated": 12,
    "uploaded_to_instantly": 12,
    "outreach": [
        {
            "contact_id": "...",
            "first_name": "...",
            "last_name": "...",
            "email": "...",
            "title": "...",
            "company_name": "...",
            "research_hook": "...",
            "research_source": "...",
            "subject": "...",
            "body": "...",
            "pain_points_addressed": [...],
            "sales_psychology": "..."
        },
        ...
    ],
    "enriched": 3,
    "skipped_no_email": 3,
}
```

### Rate Limiting

- **Apollo**: Already handled by `tenacity` retry on 429 in `apollo_client.py`
- **Web search (Firecrawl/Brave)**: No rate limiter currently; add a 1-second sleep between research calls to be safe with 10-20 leads (total ~20 seconds overhead)
- **LLM calls**: 10-20 hook extraction calls + 10-20 email generation calls = 20-40 calls. Well within rate limits.

### Error Handling

Each step degrades gracefully:
- Apollo search returns 0 contacts → return early with `contacts_found: 0`
- Enrichment fails for a contact → skip, continue with others
- Web research fails for a contact → generate email without hook (use title + company only)
- LLM email generation fails for a contact → skip, log warning, continue
- Instantly upload fails → report errors in result, don't crash

---

## Files Changed

| File | Change |
|------|--------|
| `agents/pax.py` | Add `PersonalizedOutreach` dataclass, `_research_prospect()`, `_generate_personalized_email()`, `_execute_prospect_personalize()` methods. Add `search_tools` to constructor. Add `"prospect_personalize"` to `ASSET_KEYWORDS`. Add `PERSONALIZED_EMAIL_PROMPT`. |
| `run_sales_pipeline.py` | Pass `search_tools` to Pax. Add new pipeline stage for prospect + personalize. |
| `tests/test_pax.py` | Add tests for the new flow with mocked Apollo, SearchTools, and LLM responses. |

**No new files created.** Everything goes into existing `pax.py`.

---

## Test Plan

1. **Unit: `_parse_asset_type()` keyword routing** — verify the 5 task strings from the keyword routing test matrix above all return the correct `asset_type`.
2. **Unit: `_research_prospect()`** — mock `search_tools.web_search()` and `fetch_url_content()` + LLM. Verify hook extraction. Test fallback when no results found. Test hook truncation for overly long LLM responses.
3. **Unit: `_generate_personalized_email()`** — mock LLM. Verify JSON parsing. Test with and without research hook. Test LLM returning invalid JSON.
4. **Integration: `_execute_prospect_personalize()`** — mock Apollo, SearchTools, LLM. Verify full chain produces outreach dicts with all fields populated.
5. **Edge case: no Apollo client** — returns early with `contacts_found: 0`.
6. **Edge case: no search_tools** — generates emails without research hooks (falls back to title/company personalization only).
7. **Edge case: all enrichments fail** — skips contacts without email, reports correct `skipped_no_email` count.
8. **Edge case: LLM returns invalid JSON for email** — catches exception, skips that contact, continues with others.
9. **Edge case: hook extraction returns "NO_HOOK"** — `_research_prospect` returns `("", "")`, email still generated without hook.

---

## Estimated Scope

- ~150 lines in `pax.py` (new methods + dataclass + prompt)
- ~10 lines in `run_sales_pipeline.py` (constructor change + new stage)
- ~100 lines in tests
- No new dependencies
- No schema changes
