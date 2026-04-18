# devrel-swarm → SaaS Product Design

**Date:** 2026-04-17 · **Revised:** 2026-04-18
**Author:** Daria Dovzhikova (+ Claude)
**Status:** Revision 2 approved; implementation plan at `docs/superpowers/plans/2026-04-18-devrel-swarm-v0-agentic-alpha.md`
**Codename:** TBD (working title: "the DevRel engine")

---

## Revision 2 (2026-04-18) — architectural pivot

After initial approval, the architecture was simplified from classical multi-tenant SaaS to **per-customer isolated instances orchestrated by a central control app**. Rationale: the existing `devrel-swarm` codebase was designed to be cloned + retargeted (see `CLAUDE.md` — product is switched via `product_name` config + KB swap). The original design fought that shape; Revision 2 leans into it.

**What changed:**
- **§2 Architecture:** no multi-tenant Postgres + RLS + Inngest worker fleet. Instead: each customer gets their own Fly Machine running the full repo, SQLite for persistence, HTTP-bridged MCP server for API access. The "control plane" is a thin Next.js app that provisions instances, proxies chat to their MCP server, reads dashboard data via HTTP, and writes prompt edits to their `optimize/` directory.
- **§6 Build sequence:** v0 alpha becomes ~2 weeks (down from 3). No Atlas 4-file refactor required; no Postgres schema migration; no queue layer.
- **Interface:** hybrid — dashboard for routine ops (view runs, deliverables, costs, click publish), chat (Claude Agent SDK → instance MCP tools) for configuration, optimization, and ad-hoc tasks.

**What did NOT change:**
- Target ICP (solo founders + seed-to-Series-A DevTool startups)
- Hero loop (DevRel engine: content + community)
- Agent roster (8 kept + Publisher/Seed/Mimic/Meter as MVP)
- Optimization plan (still applies, now runs per-instance)
- Pricing ($99/$299/$799 + annual) — model stays pooled-keys at the tier level (you still front Anthropic spend, instance is the fulfillment mechanism)

The sections below retain original language where still valid. Where a section is superseded, the revision note calls it out inline.

---

---

## 1. Context & TL;DR

`devrel-swarm` is a 12-agent autonomous DevRel + Sales system currently usable as a CLI tool (see `CLAUDE.md`, `README.md`). This spec turns it into a **self-serve SaaS product** sold to DevTool founders and seed-to-Series-A startups.

**Headline promise:** "Your weekly DevRel engine. Ship tutorials, triage community, keep your brand voice. No DevRel hire needed."

**TL;DR of strategic decisions:**

| Decision | Choice | Rationale |
|---|---|---|
| Product frame | SaaS self-serve | Biggest ceiling; lets product-led growth kick in |
| Target ICP | Solo DevTool founders + seed–Series-A (1–5 person teams) | Coherent band, $49–2k/mo price points, no compliance overhead |
| Hero loop | DevRel engine (content + community) | Clear positioning; Sales loop = v3 second SKU |
| API keys | Pooled (you hold), flat tiers with quotas | Removes biggest onboarding cliff; 2–3× Claude markup covers margin |
| Output delivery | Dashboard + 1-click publish to 4 channels | "Set and mostly-forget" beats "yet another draft tool" |
| Architecture | Control plane (Next.js) + agent runtime (Python workers) + Inngest queue | Clean separation; scales per-layer; matches existing OpenClaw pattern |
| Pricing | $0 trial · $99 · $299 · $799 monthly; 20% off annual | Standard SaaS ladder, ~60% target gross margin |

**MVP agent count:** 12 — 8 kept from `devrel-swarm` + 4 new (Publisher, Seed, Mimic, Meter).

**Timeline:** v0 alpha weeks 1–3 · v1 private beta weeks 4–9 · v1.1 public beta weeks 10–14 · v2 GA months 4–6 · v3 Sales SKU months 6–8. First revenue ~week 12.

---

## 2. Architecture & tenancy

> **Revision 2 supersedes the topology diagram and tenancy section below.** See `## 2b` below for the current design; the original content is preserved for rationale reference.

### 2b. Revised architecture (2026-04-18)

```
┌─ Central Next.js app (Vercel) — one deploy, you own ───────────┐
│  • Auth (NextAuth GitHub)                                       │
│  • Stripe billing (v1; v0 is internal-use only)                 │
│  • Provisioning: Fly Machines API → clone repo + push image     │
│  • Instance registry (tiny Postgres: users, instances)          │
│  • Dashboard (per-instance) reads via HTTP bridge               │
│  • Chat interface: Claude Agent SDK calls instance's MCP tools  │
│  • Prompt editor: HTTP PUT → instance's optimize/ dir           │
└──────┬──────────────────────────────────────────────────────────┘
       │ HTTPS + per-instance API token
       ▼
┌─ Customer A instance (Fly Machine) ┐  ┌─ Customer B instance ┐
│  • Full devrel-swarm repo (cloned)  │  │ • Full repo clone    │
│  • Their KB + prompts + voice       │  │ • Their everything   │
│  • SQLite on persistent volume      │  │ • SQLite             │
│  • HTTP bridge (FastAPI) → MCP tools│  │ • Same               │
│  • Cron (existing scheduler.py)     │  │ • Same               │
│  • Atlas + 12 agents (unchanged)    │  │ • Same               │
└─────────────────────────────────────┘  └──────────────────────┘
```

**Key properties:**
- Isolation is **free** — container per customer, no RLS to get right
- Existing repo **deploys as-is** — no `SharedContext` per-tenant refactor, no Atlas file split required for v0
- MCP server already exposes 14 tools; new HTTP bridge wraps those + adds deliverables/run/cost endpoints
- `optimize/{agent}/system_prompt.txt` is already the prompt override mechanism; prompt editor writes to it
- `tools/scheduler.py` already handles per-instance cron
- Central app Postgres is tiny: `users`, `instances (id, fly_app_url, api_token_encrypted, customer_id, status, provisioned_at)`, `chat_threads`, `chat_messages` (for chat history persistence)

**Provisioning flow:**
1. Customer signs up (GitHub OAuth) → onboarding wizard
2. Provisioning agent (runs in central app's Inngest or direct async task):
   - Creates Fly app under customer's slug
   - Generates API token, encrypts, stores in central Postgres
   - Builds + pushes devrel-swarm image with customer's base config baked in
   - Injects per-instance secrets via Fly secrets API (Anthropic key from pooled budget, customer OAuth tokens)
   - Runs initial KB harvest via HTTP bridge call
   - Installs cron for weekly cycle at customer's chosen cadence
3. Customer lands on dashboard, sees live instance

**Cost enforcement:**
BudgetGate still applies, but now lives *inside each customer's instance* and reads from the instance's SQLite cost_events. Central app polls instances for current month spend to surface in the dashboard + enforce plan caps at the subscription layer (e.g. refuse to provision a second instance if one is over quota, show upgrade banner at 80%).

**Trade-offs vs. original multi-tenant design:**
- Cost: ~$3–5/customer/month idle Fly compute (vs. shared pool) — acceptable at $99+ pricing
- Upgrade rollout: `fly deploy` to N instances (scripted); harder than a single SaaS deploy
- Cross-customer observability: requires central polling/aggregation, not a single DB query
- Meta-learning (autoresearch across customers): siloed — each instance's optimizer runs on its own data

---

### 2a. Original architecture (superseded — kept for reference)

#### System topology

```
┌─────────────────────┐   Stripe webhooks    ┌──────────────┐
│  Next.js 15 control │◄─────────────────────│    Stripe    │
│  plane (Vercel)     │                      └──────────────┘
│  • Auth (NextAuth)  │
│  • Dashboard UI     │   OAuth connect       ┌──────────────┐
│  • Tenant config    │◄─────────────────────►│ Substack/    │
│  • Billing/quota    │                       │ Dev.to/X/In  │
│  • KB manager       │                       └──────────────┘
│  • Publisher proxy  │
└──────────┬──────────┘
           │ enqueue job {tenant_id, job_type, params}
           ▼
┌─────────────────────┐
│     Inngest         │  durable execution, retries, cron
│  (managed queue)    │
└──────────┬──────────┘
           │ HTTP trigger
           ▼
┌─────────────────────┐   per-tenant calls    ┌──────────────┐
│  Python worker      │──────────────────────►│  Anthropic   │
│  fleet (Fly.io)     │   (via BudgetGate)    │  GitHub/FC/  │
│  • Atlas dispatcher │                       │  Apollo/etc  │
│  • 12+ agents       │
│  • BudgetGate       │   deliverables +      ┌──────────────┐
│  • KB loader        │   cost rows           │  Postgres +  │
│                     │──────────────────────►│  S3/R2       │
└─────────────────────┘                       └──────────────┘
```

### Stack

| Layer | Choice | Notes |
|---|---|---|
| Control plane | Next.js 15 App Router on Vercel | Drizzle ORM, Tailwind, shadcn/ui, NextAuth |
| Database | Postgres + pgvector | Supabase-managed or Neon; RLS enforced |
| Storage | S3 / Cloudflare R2 | Large deliverables + KB raw files |
| Queue | Inngest | Durable execution, cron, retries, native observability |
| Worker fleet | Python `product-core` on Fly.io | Per-second billing; good for bursty weekly cycles |
| Billing | Stripe Checkout + Billing | Standard SaaS plumbing |
| Secrets | Doppler (pooled keys) + AES-256-GCM in Postgres (tenant OAuth) | Master KEK in AWS KMS for production |
| Observability | Sentry + PostHog + Inngest native | Reuse PostHog already in stack |
| Email (digests) | Resend | React Email templates |

### Tenancy — row-level, single database

Every Postgres table has `tenant_id uuid not null`. RLS policy template:
```sql
USING (tenant_id = current_setting('app.tenant_id')::uuid)
```
Control plane sets `app.tenant_id` per request; workers set per job. No cross-tenant reads possible even if application code bugs out.

KB and deliverables in object storage live under `s3://bucket/tenants/{tenant_id}/...`. Worker loads per-job, never caches across tenants.

**Critical refactor:** the current `SharedContext` singleton becomes `SharedContext(tenant_id)` loaded per job from Postgres + S3. Never held between jobs.

### BudgetGate — cost-cap enforcement (load-bearing)

Every Claude call routes through `BudgetGate(tenant_id).charge(est_tokens)`:

1. Read plan quota (e.g., Starter = $30 pooled Anthropic spend/mo)
2. Read current-month spend from `cost_events`
3. If projected > cap: raise `BudgetExceeded` → Atlas pauses job → webhook to control plane → customer notified via email + in-app banner ("upgrade or wait until next cycle reset")
4. Post-call reconcile actual tokens → update `cost_events`

Non-negotiable for pooled-keys model: without it, one hallucination loop costs you a week of margin.

### Secrets model

- **Pooled API keys** (Anthropic, Firecrawl, Brave, Apollo, OpenAI embeddings): Doppler → worker env vars
- **Per-tenant OAuth tokens** (Substack, Dev.to, GitHub, LinkedIn, X): AES-256-GCM at rest in Postgres, per-tenant DEK wrapped by master KEK in AWS KMS (Doppler for MVP)
- **Future BYO-keys lane**: same path as OAuth tokens

### Job flow

1. Inngest cron ticks per tenant's timezone OR "Run now" from dashboard → control plane enqueues `weekly-cycle` job
2. Inngest triggers worker HTTP endpoint `POST /jobs/weekly-cycle` with `{tenant_id, job_id, inngest_run_id}`
3. Worker loads tenant config + KB → instantiates `Atlas(tenant_id)` → runs pipeline with BudgetGate wrapping every LLM call
4. Each stage writes checkpoint to Postgres (resumable — Inngest replays with idempotency key on crash)
5. Deliverables written to S3, rows to Postgres, webhook back to control plane
6. Control plane updates dashboard, sends digest email, enables "Publish" buttons

### Failure modes

| Failure | Behavior |
|---|---|
| Worker crash mid-run | Inngest replays from last checkpoint (reuse existing Atlas checkpoint logic) |
| Tenant exceeds budget mid-job | Graceful pause, partial output preserved, customer notified |
| Upstream API down (GitHub, Firecrawl) | Existing graceful-degradation logic kept; agent logs skipped inputs |
| Publisher fails (Substack rejects) | Draft stays in dashboard, retry button + error surfaced, no silent loss |

---

## 3. Agent roster

### Existing agents — kept in MVP (8)

| Agent | Role | MVP changes |
|---|---|---|
| **Atlas** | Orchestrator | Split 570-LOC file into `orchestrator.py` + `memory.py` + `checkpoints.py` + `dispatch.py`; per-tenant SharedContext; Postgres-backed checkpoints |
| **Watchdog** | Health | Rescoped per-tenant: OAuth validity, integration reach, quota headroom (global infra now handled by Inngest) |
| **Sage** | GitHub triage | Haiku batch classification; priority/label rows persist to Postgres for dashboard filters |
| **Echo** | Social listener (Reddit/HN/X) | Anthropic Batch API for sentiment (50% cost); per-platform rate-limit respect; normalized engagement scores |
| **Iris** | Feedback synthesizer | pgvector similarity for cross-week recurring-theme detection (replaces rule-based dedup) |
| **Kai** | Content generator | Prompt caching on voice profile + KB + prior outputs; critique = Haiku / revision = Sonnet / Opus only if stuck <7; autoresearch-tuned first |
| **Sentinel** | Brand auditor | Adds 7th dimension (Mimic voice audit); scores persist to `quality_events` for trend line |
| **Rex** | Competitive intel (backing) | Domain-level search result cache; no user-facing surface in v1 |

### New agents — MVP (4)

1. **Publisher** — ships Kai output to 4 channels (Substack, Dev.to, LinkedIn, X). Handles OAuth refresh, retry-on-fail, scheduling (native where supported, internal queue for LinkedIn/X), edit-after-publish. *Why MVP:* "1-click publish" is the core UX bet.

2. **Seed** — onboarding agent. Validates OAuth, harvests initial KB, generates a real preview deliverable within ~10 minutes on sampled-from-GitHub signals. Cost capped at ≤$0.50/preview. *Why MVP:* without fast preview, trial conversion collapses.

3. **Mimic** — voice-profile agent. Ingests customer's blog RSS + Twitter export + GitHub READMEs + podcast transcripts; builds structured profile (tone, vocabulary, sentence patterns, taboo phrases). Kai writes against it; Sentinel audits against it. *Why MVP:* primary differentiator vs. "ChatGPT + prompt."

4. **Meter** — billing/usage agent. Wraps BudgetGate; surfaces usage in dashboard; enforces quotas; handles Stripe webhook reconciliation and plan upgrades. *Why MVP:* SaaS table stakes.

### New agents — v1.1 (3)

5. **Ledger** — analytics/attribution. PostHog/Plausible/GA integration; reports "this week's content drove N pageviews, M signups, K stars." *Gates to month-3 retention.*
6. **Curator** — KB maintainer. Monthly re-harvest, staleness diff, incremental pgvector updates.
7. **Valet** — reply triage. Watches published-piece comment threads; routes noteworthy replies to customer with suggested responses.

### New agents — v2 (3)

8. **Pulse** — customer success / churn-risk monitor.
9. **Aid** — product-support triage (answers customer questions about the product itself).
10. **Compass** — proactive editorial planner (4-week lookahead).

### Deferred existing agents

- **Nova** (growth experiments) → v2 Pro+ add-on
- **Dex** (doc generator) → v2 Pro+ add-on
- **Vox** (video) → v3 add-on, only if screen-record tech matures
- **Pax** + **Mox** → v3 as part of "Sales loop" SKU

### Publisher OAuth/API surface

| Channel | Auth | Publish | Edit-after | Schedule |
|---|---|---|---|---|
| Substack | OAuth 2 (API v1) | POST /api/v1/posts | PATCH | native |
| Dev.to | OAuth 2 | POST /api/articles | PUT | `published_at` future |
| LinkedIn | OAuth 2 (personal + org) | UGC endpoint | delete + repost | internal queue |
| X | OAuth 2 (user context) | POST /2/tweets | delete only | internal queue |

Build order to de-risk: **Substack + LinkedIn first**, Dev.to + X as later slices of v1 (X rate limits are notoriously erratic).

---

## 4. Optimization plan (existing agents)

### Cross-cutting

1. **Retarget autoresearch optimizer** — current `optimize/` tunes Pax (deferred). Retarget to Kai first, Sage second, Iris third. Sentinel stays unoptimized (fixed-evaluator principle).
   - Kai criteria: voice fidelity vs. Mimic, technical accuracy, hook strength, actionability, absence-of-AI-slop-tells
   - Sage criteria: priority accuracy, sentiment F1, churn-risk precision/recall
   - Iris criteria: theme coherence, redundancy rate, cross-week continuity
   - Per-tenant overrides at `optimize/agents/{agent}/tenants/{tenant_id}/system_prompt.txt`

2. **Cost discipline** (biggest margin lever):
   - Model routing: Haiku 4.5 classification / Sonnet 4.6 default / Opus only on stuck Kai revision
   - Prompt caching on voice profile + KB + prior outputs in Kai loop (5-min TTL)
   - Anthropic Batch API for Sage + Echo classification (N > 100)
   - BudgetGate pre-call estimate + post-call reconcile

3. **Real parallelism** — replace intra-process `asyncio.gather()` with Inngest fan-out/fan-in: each parallel-stage agent = separate Inngest step. Central Redis-backed token-bucket rate limiter per upstream API; per-tenant concurrency cap (default 1 in-flight cycle).

4. **KB migration** — markdown files → pgvector with per-tenant RLS. Seed harvests on onboarding (productize `kb_harvester.py` behind UI). Kai retrieves via semantic search instead of TF-IDF.

5. **Infra cleanup** — drop `aiohttp` + `requests`, standardize on `httpx`. Context archive per-tenant S3 paths with rolling compaction (keep 12 weeks dense + every 4th beyond); lifecycle → Glacier after 90d.

6. **Observability** — per-tenant dashboard panel (status, Sentinel trend, cost, quota, health); global ops view (tenant matrix, budget exceptions, failed runs, lowest-scoring outputs for golden-set curation); Sentry + PostHog + Inngest native.

7. **Quality regression gate** — golden test set per agent + pytest + `respx` (already in repo) + semantic similarity check. Block deploys when Kai <7/10 on golden set.

8. **Dry-run mode** — onboarding preview via fixtures (≤$0.50); power-user "preview a topic" button in dashboard.

### Per-agent (summary)

See §3 table above — each kept agent has a specific change listed.

### Expected impact

- Per-tenant weekly-cycle cost: est. **$8–12 → $3–5** (routing + caching + batch)
- Quality floor: Sentinel-enforced 7/10 gate + semantic regression tests
- Cycle latency: ~30% shorter via Inngest parallelism
- Operational risk: BudgetGate + rate limiter + RLS = no runaway bills, no noisy neighbors

---

## 5. User journey + data model

### Activation journey

Target: preview < 10 min, first publish < 48h.

```
1. Land → signup (Google/GitHub OAuth)           │ < 30s
2. Product basics (name, category, ICP, position)│ < 2min
3. Connect GitHub repo(s) (OAuth)                │ < 30s
4. Voice sources (RSS + Twitter export + podcast)│ < 2min
   └─ Mimic ingests in background
5. Connect ≥1 publisher channel (require ≥1)     │ < 1min
6. Cadence + timezone                            │ < 30s
7. ━━━━━━ PREVIEW (Seed runs) ━━━━━━━━━━━━━━━━━  │ < 10min
   └─ Real tutorial draft + voice score shown
8. Paywall (Stripe Checkout, 14-day trial)       │
9. First live weekly cycle on scheduled tick     │
10. Monday 9am: email digest + dashboard loaded   │
11. Review → edit → 1-click publish per channel   │
```

**Load-bearing assertion:** preview output within 10 minutes at $0 to tenant. Seed uses fixtures + pooled-token budget ≤ $0.50 per preview.

### Dashboard surfaces (v1)

1. **Home** — this-week status, next-run countdown, recent deliverables, Sentinel trend
2. **Deliverables** — list + filters; per-item preview/edit/publish/archive
3. **Integrations** — OAuth health, reconnect button
4. **Voice profile** — Mimic profile viewer, tone/formality/technicality/humor sliders, "add source" button
5. **Knowledge base** — harvested docs, re-harvest trigger, last-fetched timestamps
6. **Usage + billing** — plan, this-month pooled spend vs. cap, cost trend, upgrade CTA
7. **Settings** — cadence, timezone, team seats

### Pricing tiers

| Plan | Monthly | Annual (−20%) | Pooled spend cap | Cycles | Outputs/cycle | Channels | Voice tuning |
|---|---|---|---|---|---|---|---|
| Trial | $0 (14d) | — | $5 | 1 preview + 2 cycles | limited | 1 | auto |
| Starter | $99 | $948/yr | $30 | 4 weekly | 1 tutorial + digest + 5 social | 2 | auto |
| Pro | $299 | $2,868/yr | $100 | 4 weekly | 1 tutorial + 2 shorts + digest + 10 social | 4 | auto + manual |
| Scale | $799 | $7,668/yr | $350 | 8 (bi-weekly) | 2 tutorials + 4 shorts + digest + 20 social | 4 + API | full control |

Quota metered in pooled Anthropic spend (drives margin); output count is a soft UI cap.

### Postgres schema (key tables)

Every table below has `tenant_id uuid not null` + RLS policy.

**Core**
- `tenants` — plan, Stripe IDs, quota cap, cadence, timezone
- `users` — email, role (owner/member)
- `product_profile` — name, category, ICP, positioning (1:1 with tenant)

**Integrations & secrets**
- `integrations` — OAuth tokens AES-256-GCM encrypted; kind ∈ {github, discord, slack, substack, devto, linkedin, x, posthog}
- `voice_profile` — Mimic JSON + source refs + version

**Content pipeline**
- `kb_documents` — text + `tsvector` + `vector(1536)` pgvector
- `signals` — raw signals from Sage/Echo
- `themes` — Iris output, ISO-week keyed, links to signals
- `deliverables` — Kai output + scores + status
- `publications` — per-channel publish records

**Execution & ops**
- `jobs` — Inngest run ID + status + total cost
- `job_checkpoints` — per-stage payloads (resumable)
- `cost_events` — per-LLM-call tokens + cost (for BudgetGate + dashboard)
- `quality_events` — per-deliverable per-dimension Sentinel scores

**Critical indexes**
- `signals (tenant_id, ingested_at desc)`
- `deliverables (tenant_id, status, created_at desc)`
- `cost_events (tenant_id, date_trunc('month', created_at))`
- `kb_documents` ivfflat on embedding, per-tenant partition

**Storage split** — large payloads (tutorial markdown, preview artifacts) → S3 at `tenants/{id}/deliverables/{id}.md`; Postgres stores `s3_key`. Structured + small stays in Postgres.

---

## 6. Build sequence

> **Revision 2 note:** v0 collapsed from 3 weeks to ~2 weeks. Sections below are from the original plan; the live roadmap is in `docs/superpowers/plans/2026-04-18-devrel-swarm-v0-agentic-alpha.md`. v1/v1.1/v2/v3 phasing below remains valid at the feature-set level — only v0's mechanics changed.

### v0 — Private alpha (weeks 1–2) — REVISED

**Goal (unchanged):** Daria runs OpenClaw's DevRel loop through the product, end-to-end.

**Mechanics (revised):**
- Package devrel-swarm as a deployable instance: add SQLite storage, HTTP bridge around MCP server, auth-token middleware, Dockerfile with persistent volume
- Build thin central Next.js app: auth, instance registry, dashboard (HTTP-reads), chat (Claude Agent SDK → instance MCP), prompt editor (HTTP-writes to `optimize/`)
- Provisioning: Fly Machines API client + "Add instance" flow
- Manual v0 option: paste Fly app URL + API token (for first OpenClaw instance before full provisioning agent lands)

**Gate to v1 (unchanged):** one full weekly cycle runs end-to-end without manual intervention; cost tracking matches Anthropic invoice within 5%.

---

### v0 — Private alpha (weeks 1–3) — ORIGINAL (superseded)

**Goal:** Run OpenClaw's DevRel loop through the product, end-to-end. Single tenant, no billing.

- Fork `devrel-swarm` → `product-core` (workers repo)
- Next.js + Postgres + NextAuth skeleton (control plane repo)
- Inngest cron + HTTP worker endpoint
- Strip `SharedContext` singleton → per-job instance
- Split `atlas.py` into 4 files
- Manual tenant creation via SQL script
- Dashboard: Home + Deliverables list only
- BudgetGate stubbed (tracks, doesn't block)

**Gate to v1:** one full weekly cycle runs end-to-end without manual intervention; cost tracking matches Anthropic invoice within 5%.

### v1 — Private beta (weeks 4–9)

**Goal:** 10 invited founders running the loop. Free for 4 weeks.

- RLS + `tenant_id` everywhere; GUC-setting middleware
- Onboarding wizard (7 steps, <10 min preview)
- New MVP agents: Publisher (all 4 channels), Seed, Mimic, Meter
- Kept-agent refactors: Atlas split, Watchdog rescoped, Sage/Echo Haiku batch, Iris pgvector dedup, Kai caching + model routing, Sentinel Mimic dimension
- pgvector KB + migration from markdown
- Stripe + tiers + quota enforcement; real BudgetGate
- Autoresearch retargeted to Kai
- Dashboard: all 7 surfaces
- Sentry + PostHog observability
- Closed via invite codes

**Gate to v1.1:** ≥5/10 tenants publishing weekly by end of week 9; Sentinel floor holds (no <7/10 shipped); per-tenant cost ≤$5/cycle.

### v1.1 — Public beta (weeks 10–14)

**Goal:** Open signups. Trial → paid funnel live.

- Stripe annual plans + 20% discount
- New agents: Ledger, Valet, Curator (automated monthly re-harvest)
- Autoresearch extended to Sage + Iris
- Public pricing page, landing page, comparison doc vs. "hiring a DevRel"
- Referral codes (free month for referrer + referee)
- Onboarding preview hardening

**Gate to v2:** gross margin ≥50% at ~30 tenants; trial→paid conversion ≥15%.

### v2 — GA + growth (months 4–6)

**Goal:** 100 paying tenants, team-ready.

- New agents: Pulse, Aid, Compass
- Unlock Nova + Dex as Pro+ add-ons
- BYO API keys (Pro+)
- Team seats + roles + audit log
- Public API for Scale tier
- SOC2 Type I prep if enterprise signals

### v3 — Sales SKU (months 6–8)

- Unlock Pax + Mox + new SDR/meeting-prep agents as "Sales loop" SKU
- Shared tenant, separate subscription, cross-sell flow
- Vox as optional add-on if tech matures

### Parallel tracks (non-blocking)

- Content marketing (build-in-public blog, thesis pieces)
- Design system polish (shadcn baseline until v1.1)
- CI/CD hardening (GH Actions → Vercel preview + worker staging)
- Golden test set curation (grows with alpha feedback)

### Build-sequence risks

| Risk | Mitigation |
|---|---|
| Publisher OAuth breakage per-channel (esp. X rate limits + LinkedIn personal/org split) | Substack + LinkedIn first; Dev.to + X later slices of v1 |
| Mimic voice quality determines product reputation | +1 week iteration budget; 5 hand-labeled golden profiles before launch |
| Pooled-key cost overrun mid-alpha | v0: manual daily spend check with $500/week external ceiling (BudgetGate is tracking-only until v1). v1: BudgetGate hard-stop enforced |
| Inngest vendor lock-in | Queue abstraction layer (`JobDispatcher` interface); swap ≈ 2 weeks |
| OpenClaw network pulls focus | Dedicated "OpenClaw day" per week; hard boundary |

### Timeline summary (solo + Claude Code agents)

- Weeks 1–3: v0 alpha
- Weeks 4–9: v1 private beta (1 wk buffer)
- Weeks 10–14: v1.1 public beta
- Months 4–6: v2 GA
- Months 6–8: v3 Sales SKU

First revenue: ~week 12.

---

## 7. Open questions (carry into plan-writing)

1. **Worker host** — Fly.io selected (per-second Python containers, bursty-friendly). Final validation: run a cost model against OpenClaw's weekly-cycle token/compute profile before v0 worker deploy; bail to Railway if Fly.io billing surprises appear.
2. **KB embedding model** — Voyage-3 (quality leader, +1 vendor) vs. OpenAI text-embedding-3-large vs. Cohere embed-v3. Decision needed before pgvector schema is frozen in v1 week 4.
3. **Email delivery** — Resend selected (DX + React Email). Final validation: deliverability benchmarking (inbox placement, DKIM/SPF) before public beta in week 10.
4. **Voice profile schema** — commit to v1 schema and migrate later, or flexible JSON schema-on-read. Decision: **flexible JSON for v1**; lock schema in v2 after ≥20 tenants inform the shape.
5. **Product name** — TBD; branding exercise + domain availability search needed before public beta in week 10.

---

## 8. Success metrics

| Phase | Metric | Target |
|---|---|---|
| v0 alpha exit | End-to-end cycle without intervention | 1 full run |
| v0 alpha exit | Cost tracking accuracy | ±5% vs. Anthropic invoice |
| v1 beta exit | Tenants publishing weekly | ≥5/10 |
| v1 beta exit | Sentinel floor breaches | 0 shipped <7/10 |
| v1 beta exit | Per-tenant weekly cost | ≤$5 |
| v1.1 exit | Gross margin at ~30 tenants | ≥50% |
| v1.1 exit | Trial → paid conversion | ≥15% |
| v2 exit | Paying tenants | 100 |
| v2 exit | Monthly recurring revenue | ≥$20k MRR |

---

## 9. Out of scope for this spec

- White-label / agency multi-workspace (future)
- Self-hosted deploy option (future)
- SOC2 / SSO / enterprise compliance (v2+ if signals warrant)
- Sales SKU agents (v3, separate spec)
- Video production (Vox) productization (v3+, tech must mature first)

---

## 10. Next step

Hand off to `writing-plans` skill to produce the implementation plan, starting with v0 alpha as the first executable phase. Each subsequent phase (v1, v1.1, v2, v3) gets its own plan document when we reach that gate.
