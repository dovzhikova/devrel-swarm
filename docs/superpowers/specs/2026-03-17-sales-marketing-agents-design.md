# Sales & Marketing Agents — Design Spec

**Date:** 2026-03-17
**Author:** Daria Dovzhikova + Claude
**Status:** Draft

---

## Goal

Add three new agents — Rex (Competitive Intelligence), Pax (Sales Enablement), and Mox (Campaign Marketing) — to the existing devtools-advocate-agent system. These agents generate sales and marketing assets for whatever product the system is pointed at (OpenClaw, PostHog, etc.), using the same tools and patterns as existing agents.

## Architecture

Pipeline-first design. Rex runs weekly as part of the Atlas pipeline. Pax and Mox are on-demand, triggered via CLI.

```
Sage ──┐
Echo ──┼→ Iris → Nova → Kai → Vox/Dex
       │
       └→ Rex (competitive intel, weekly)
              ↓ (SharedContext)
         Pax (sales enablement, on-demand)
         Mox (campaign marketing, on-demand)
```

All three agents follow the existing pattern:
- `__init__(api_client, knowledge_base_path, ...)` constructor
- `async execute(task, context) -> dict` interface
- Dataclass DTOs for structured output
- Async throughout, httpx for HTTP, logging over print

## Constraints

- Use existing tools only (knowledge base, GitHub, web search). No CRM or analytics integrations.
- All output is markdown deliverables + structured dicts in SharedContext.
- Follow existing coding conventions (async, type hints, dataclasses, httpx, line length 100).
- Each agent is a single focused file under `agents/`.

---

## Agent 1: Rex — Competitive Intelligence

### Purpose

Weekly competitive landscape monitoring. Identifies what competitors are doing, where they're strong/weak, and what the target product should do about it.

### Schedule

Weekly — Stage 2b in Atlas pipeline, after Echo and Sage, alongside Iris.

### Upstream Context

- `echo_social` — social mentions that reference competitors
- `sage_triage` — GitHub issues where users mention competitor products
- Knowledge base — product positioning, feature set for comparison

### Dataclasses

```python
@dataclass
class CompetitorProfile:
    """A tracked competitor and their current market position."""
    name: str
    domain: str
    category: str               # e.g., "ai-assistant", "chatbot-platform"
    strengths: list[str]
    weaknesses: list[str]
    recent_moves: list[str]     # product launches, pricing changes, announcements

@dataclass
class MarketPosition:
    """How a competitor positions themselves."""
    competitor: str
    positioning_statement: str
    differentiators: list[str]
    pricing_tier: str           # "free", "freemium", "paid", "enterprise"
    target_audience: str

@dataclass
class Threat:
    """A competitive threat."""
    competitor: str
    threat: str
    severity: str               # "high", "medium", "low"

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
```

### Competitor Discovery

Competitors are discovered from the knowledge base. The agent scans all KB files for
competitor mentions using keyword patterns ("vs", "alternative", "compared to", "competitor").
Additionally, competitor names can be passed explicitly in the task string:
`--agent rex --task "Competitive analysis for: Botpress, Rasa, Voiceflow"`.

### Execute Logic

1. Extract competitor names from knowledge base (scan for "vs", "alternative", "compared to"
   patterns) or parse them from the task string.
2. For each competitor:
   a. Search web for recent news/announcements (via SearchTools.web_search).
   b. Search web for competitor GitHub activity (e.g., "site:github.com {competitor} releases").
      Note: use web_search, NOT GitHubTools directly (which is scoped to the target repo only).
   c. Cross-reference Echo's social mentions for competitor sentiment.
   d. Cross-reference Sage's issues for users mentioning competitor products.
3. Build CompetitorProfile and MarketPosition for each.
4. Identify threats (competitors gaining ground) and opportunities (gaps in offerings).
5. If LLM client available, generate a narrative competitive report with recommended responses.
6. If web search or any external call fails, degrade gracefully — return partial results
   based on knowledge base and upstream context alone (same pattern as Sage without GitHub token).
7. Return structured dict for SharedContext.

### Deliverable

`deliverables/competitive-intel.md`:

```markdown
# Competitive Intelligence Report — Week of {date}
## Market Movements
## Positioning Map
## Threats
## Opportunities
## Recommended Responses
```

### System Prompt

```
You are Rex, a competitive intelligence analyst for {product_name}. Your role is
to monitor the competitive landscape and produce actionable intelligence that
informs sales positioning, product strategy, and marketing messaging.

You produce:
- Weekly competitive landscape reports
- Competitor profiles with strengths/weaknesses
- Threat assessments with severity ratings
- Opportunity identification with recommended responses

Ground all analysis in evidence: social mentions, GitHub activity, web search
results, and knowledge base comparisons. Never speculate without data.
```

---

## Agent 2: Pax — Sales Enablement

### Purpose

On-demand sales asset generation. Produces outreach emails, battle cards, nurture sequences, and objection handling docs.

### Schedule

On-demand only — triggered via `python -m agents.atlas --agent pax --task "..."`.

### Upstream Context

- `rex_competitive` — competitor profiles, positioning, threats/opportunities
- `iris_themes` — real developer pain points for personalized outreach
- `sage_triage` — actual GitHub issues for objection handling evidence
- Knowledge base — product features, pricing, value propositions

### Dataclasses

```python
@dataclass
class OutreachEmail:
    """A personalized outreach email."""
    subject: str
    body: str
    personalization_hooks: list[str]   # why this matters to the recipient
    pain_points_addressed: list[str]
    cta: str

@dataclass
class BattleCard:
    """One-page competitive comparison document."""
    competitor: str
    comparison_table: dict[str, dict[str, str]]  # feature -> {us: ..., them: ...}
    objection_responses: list[dict[str, str]]     # {objection: ..., response: ...}
    win_themes: list[str]
    proof_points: list[str]

@dataclass
class NurtureSequence:
    """Multi-step email drip campaign."""
    segment: str                 # target audience segment
    goal: str                   # what the sequence aims to achieve
    cadence_days: list[int]     # days between emails, e.g. [0, 3, 7, 14, 21]
    emails: list[OutreachEmail]

@dataclass
class SalesAsset:
    """Generic sales document."""
    title: str
    asset_type: str             # "one-pager", "pitch-deck-outline", "objection-doc"
    body: str
    target_persona: str
    target_vertical: str
```

### Task Parsing

Pax determines asset type from the task string using keyword matching (same approach as
Sage's `_categorize_issue`). Keywords:

- "outreach" / "email" / "prospect" → outreach emails
- "battle card" / "vs" / "comparison" → battle card
- "nurture" / "drip" / "sequence" → nurture sequence
- "one-pager" / "one pager" / "summary" → sales one-pager
- "objection" / "FAQ" / "pushback" → objection handling doc

If no keyword matches, defaults to a general sales asset.

### Execute Logic

1. Parse task string to determine asset type via keyword matching (see above).
2. Search knowledge base for relevant product info (features, pricing, value props).
   Note: Pax does not need search_tools — it uses knowledge_base_path directly (like Kai).
3. Pull Rex's competitive data from context for battle cards and positioning.
4. Pull Iris's pain points from context for outreach personalization.
5. Pull Sage's issues for real-world objection evidence.
6. Generate asset via LLM, grounded in all upstream context.
7. Return structured dict with the generated asset.

### Deliverables

On-demand assets are written to `deliverables/sales/{asset_type}-{date}.md`, e.g.:
- `deliverables/sales/battle-card-2026-03-17.md`
- `deliverables/sales/outreach-2026-03-17.md`
- `deliverables/sales/nurture-sequence-2026-03-17.md`

### Example Tasks

```bash
# Outbound prospecting
--agent pax --task "Generate outreach emails for DevOps engineers evaluating observability tools"

# Battle cards
--agent pax --task "Create a battle card: OpenClaw vs Botpress"

# Nurture sequences
--agent pax --task "Write a 5-email nurture sequence for trial users who haven't connected a channel"

# Sales one-pager
--agent pax --task "Create a one-pager for enterprise CTOs evaluating AI assistant platforms"

# Objection handling
--agent pax --task "Write objection handling doc for 'why not just use ChatGPT directly?'"
```

### System Prompt

```
You are Pax, a sales enablement specialist for {product_name}. Your role is to
produce sales assets that help close deals: outreach emails, battle cards,
nurture sequences, one-pagers, and objection handling docs.

Guidelines:
1. EVIDENCE-BASED — Ground every claim in knowledge base facts, competitive
   data, or real community pain points. No empty marketing speak.
2. DEVELOPER-AWARE — The buyer is often a developer or technical leader.
   Respect their intelligence. Lead with value, not hype.
3. PERSONALIZED — Use upstream pain points and competitive gaps to make
   outreach specific and relevant to the recipient's situation.
4. ACTIONABLE — Every asset should have a clear CTA and next step.
5. HONEST — Never misrepresent capabilities. Acknowledge limitations when
   they exist — credibility matters more than closing one deal.
```

---

## Agent 3: Mox — Campaign Marketing

### Purpose

On-demand marketing campaign and content generation. Produces SEO blog posts, landing page copy, social media batches, launch campaigns, press releases, and case study frameworks.

### Schedule

On-demand only — triggered via `python -m agents.atlas --agent mox --task "..."`.

### Upstream Context

- `rex_competitive` — competitive positioning for differentiation in content
- `iris_themes` — developer pain points to address in marketing content
- `kai_content` — existing tutorials to reference/repurpose in blog posts
- Knowledge base — product features, messaging, brand voice

### Dataclasses

```python
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
    features: list[dict[str, str]]    # {title: ..., description: ..., icon_hint: ...}
    social_proof: list[str]           # testimonial snippets or stats
    cta_primary: str
    cta_secondary: str
    seo_title: str
    seo_description: str

@dataclass
class SocialBatch:
    """A batch of platform-specific social media posts."""
    platform: str              # "twitter", "linkedin", "reddit"
    campaign_name: str
    posts: list[dict[str, str]]  # {text: ..., hook: ..., cta: ...}
    hashtags: list[str]

@dataclass
class CampaignBrief:
    """Full product launch or marketing campaign brief."""
    name: str
    goal: str
    positioning: str
    messages: list[str]          # messaging hierarchy (primary, secondary, tertiary)
    channels: list[str]          # twitter, blog, email, product-hunt, etc.
    timeline: list[dict[str, str]]  # {day: ..., action: ..., owner: ...}
    draft_assets: list[str]      # list of asset descriptions to produce

@dataclass
class PressRelease:
    """Structured press release."""
    headline: str
    subhead: str
    body: str
    quotes: list[dict[str, str]]  # {speaker: ..., title: ..., quote: ...}
    boilerplate: str
    contact: str
```

### Task Parsing

Mox determines content type from the task string using keyword matching:

- "blog" / "SEO" / "article" → blog post
- "landing page" / "landing copy" → landing page copy
- "social" / "twitter" / "linkedin" / "reddit" → social media batch
- "launch" / "campaign" → campaign brief
- "press release" / "announcement" → press release
- "case study" / "customer story" → case study framework

If no keyword matches, defaults to a blog post.

### Execute Logic

1. Parse task string to determine content type via keyword matching (see above).
2. Search knowledge base for product messaging, features, brand voice.
3. Pull Rex's competitive data for differentiation angles.
4. Pull Iris's themes for pain-point-driven messaging.
5. Pull Kai's content for tutorial references in blog posts.
6. Generate content via LLM, grounded in upstream context.
7. Validate any code blocks via CodeValidator (for technical blog posts).
8. Return structured dict with the generated content.

### Deliverables

On-demand content is written to `deliverables/campaigns/{content_type}-{date}.md`, e.g.:
- `deliverables/campaigns/blog-post-2026-03-17.md`
- `deliverables/campaigns/landing-page-2026-03-17.md`
- `deliverables/campaigns/social-batch-2026-03-17.md`

### Example Tasks

```bash
# SEO blog posts
--agent mox --task "Write an SEO blog post: 'Best Open-Source AI Assistants in 2026'"

# Landing page copy
--agent mox --task "Write landing page copy for OpenClaw's WhatsApp integration"

# Social media
--agent mox --task "Generate a week of social media posts highlighting developer pain points"

# Launch campaigns
--agent mox --task "Create a product launch campaign for OpenClaw voice support"

# Press releases
--agent mox --task "Write a press release announcing OpenClaw's 1.0 release"

# Case studies
--agent mox --task "Create a case study framework for a DevOps team using OpenClaw for incident response"
```

### System Prompt

```
You are Mox, a campaign marketing specialist for {product_name}. Your role is
to produce marketing content and campaigns that drive awareness, engagement,
and conversion among developers and technical decision-makers.

Guidelines:
1. DEVELOPER-AUTHENTIC — Write like a developer advocate, not a marketer.
   No buzzwords, no fluff. Technical audiences smell inauthenticity instantly.
2. SEO-AWARE — Structure blog posts with clear H2/H3 hierarchy, include
   target keywords naturally, write compelling meta descriptions.
3. PAIN-POINT-DRIVEN — Every piece of content should address a real developer
   frustration identified by upstream agents, not invented marketing problems.
4. DIFFERENTIATED — Use competitive intelligence to position against
   alternatives. Show don't tell — concrete features, not vague claims.
5. MULTI-FORMAT — Adapt messaging for each platform's conventions. Twitter
   threads != LinkedIn posts != Reddit comments.
```

---

## Pipeline Integration

### SharedContext Changes

Add three new fields and update `to_dict()` to include them:

```python
@dataclass
class SharedContext:
    week_of: str = ""
    sage_triage: dict[str, Any] = field(default_factory=dict)
    echo_social: dict[str, Any] = field(default_factory=dict)
    iris_themes: dict[str, Any] = field(default_factory=dict)
    nova_experiments: dict[str, Any] = field(default_factory=dict)
    kai_content: dict[str, Any] = field(default_factory=dict)
    vox_video: dict[str, Any] = field(default_factory=dict)
    dex_docs: dict[str, Any] = field(default_factory=dict)
    rex_competitive: dict[str, Any] = field(default_factory=dict)    # NEW
    pax_sales: dict[str, Any] = field(default_factory=dict)          # NEW
    mox_campaigns: dict[str, Any] = field(default_factory=dict)      # NEW
    okr_progress: dict[str, Any] = field(default_factory=dict)
```

**Important:** `to_dict()` must also be updated to include `rex_competitive`, `pax_sales`,
and `mox_campaigns`. The current implementation manually lists every field rather than using
`dataclasses.asdict()`. Either add the three new fields to the manual list, or refactor
`to_dict()` to use `dataclasses.asdict(self)` to prevent this class of bug in the future.

### Weekly Pipeline Order

```
Stage 1:  Sage (GitHub triage)
Stage 1b: Echo (social listening)
Stage 2:  Iris (feedback synthesis)        ← uses Sage + Echo
Stage 2b: Rex (competitive intelligence)   ← uses Echo + Sage + web search  [NEW]
Stage 3:  Nova (growth experiments)        ← uses Iris
Stage 3b: Dex (source code docs)
Stage 4:  Kai (content creation)           ← uses Iris + Dex
Stage 4b: Vox (video)                      ← uses Kai
Stage 5:  Atlas compiles OKRs
```

Pax and Mox are NOT in the weekly cycle. They are on-demand only.

### Atlas Registration

```python
# In Atlas.__init__():
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

self._agents = {
    # ... existing ...
    "rex": self.rex,
    "pax": self.pax,
    "mox": self.mox,
}
```

### OKR Additions

```python
def _compile_okrs(self) -> dict[str, Any]:
    return {
        # ... existing ...
        "competitors_tracked": len(self.context.rex_competitive.get("profiles", [])),
        "threats_identified": len(self.context.rex_competitive.get("threats", [])),
        "opportunities_found": len(self.context.rex_competitive.get("opportunities", [])),
    }
```

---

## File Changes Summary

| File | Change | Est. Lines |
|------|--------|-----------|
| `agents/rex.py` | New — Competitive Intelligence agent | ~300 |
| `agents/pax.py` | New — Sales Enablement agent | ~350 |
| `agents/mox.py` | New — Campaign Marketing agent | ~350 |
| `agents/atlas.py` | Add 3 SharedContext fields, register 3 agents, add Rex to weekly cycle, update OKRs | ~40 |
| `agents/__init__.py` | Export Rex, Pax, Mox | ~3 |
| `tests/test_rex.py` | Unit tests for Rex | ~200 |
| `tests/test_pax.py` | Unit tests for Pax | ~200 |
| `tests/test_mox.py` | Unit tests for Mox | ~200 |

**Total new code:** ~1,650 lines across 6 new files + ~40 lines of changes to existing files.

---

## Testing Strategy

Each agent gets tests covering:

1. **Dataclass construction** — all DTOs instantiate correctly with required fields.
2. **Knowledge base search** — verify the agent finds relevant docs for its domain.
3. **Upstream context extraction** — verify it correctly reads Rex/Iris/Sage data from SharedContext.
4. **Execute without LLM** — returns structured result with prompt_used (graceful degradation).
5. **Execute with mock LLM** — returns generated content in the expected structure.
6. **Atlas integration** — agent is registered, SharedContext field exists, to_dict includes it.

Test fixtures: canned competitive data, sample outreach tasks, mock social mentions with competitor references.

---

## Success Criteria

1. `python -m agents.atlas --weekly-cycle` runs and produces `competitive-intel.md` alongside existing deliverables.
2. `python -m agents.atlas --agent pax --task "..."` generates sales assets grounded in upstream context.
3. `python -m agents.atlas --agent mox --task "..."` generates marketing content grounded in upstream context.
4. All tests pass: `pytest tests/test_rex.py tests/test_pax.py tests/test_mox.py -v`.
5. No PostHog references in any new agent code (product-agnostic, reads from knowledge base).
