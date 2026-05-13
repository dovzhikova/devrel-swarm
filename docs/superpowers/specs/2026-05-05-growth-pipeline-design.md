# Growth pipeline (Selene/Vega/Cyra) — design spec

**Status:** Approved (2026-05-05)
**Author:** Daria Dovzhikova
**Target version:** v0.3.0 (~late May 2026)
**Predecessor:** Argus (v0.2.4) — same auditor pattern generalized to three new pillars

## 1. Why this exists

devrel-origin ships at v0.2.4 with one auditor in the post-publish slot (Argus, content performance). Brand presence in 2026 has three more measurement surfaces that aren't covered: organic search rankings, AI-engine citations, and conversion funnel diagnosis. Each is a discipline with mature toolchains; none currently feed into the swarm's recommendation pipeline.

This spec adds three new auditor agents — **Selene** (SEO), **Vega** (GEO), **Cyra** (CRO) — as a fourth pipeline alongside Health, DevRel, and Sales. All three follow Argus's pattern exactly: gather signals, score deterministically, emit structured `Recommendation` rows, stage Mox-ready briefs. None of them write content or push to external systems; that stays Mox's job.

The combined output answers the question "what should we ship next?" across four signal sources (post-publish content perf, organic search, AI-engine visibility, on-site conversion) instead of just one.

## 2. Architecture

```
Atlas (orchestrator)
│
├── Health     · Watchdog · Sentinel · Argus
├── DevRel     · Sage · Echo · Iris · Nova · Kai · Vox · Dex
├── Sales      · Rex · Pax · Mox
└── Growth     · Selene (SEO) · Vega (GEO) · Cyra (CRO)   ← new
```

**Pipeline placement:** Atlas Stage 5c (post-publish), parallel to Argus's Stage 5b. Per-pillar gates: `[orchestration].argus_in_run` / `seo_in_run` / `geo_in_run` / `cro_in_run`. Defaults: argus + cro ON, seo + geo OFF (the heavies are opt-in). Within Stage 5c, all three pillars run concurrently via `asyncio.gather(selene, vega, cyra)` — they have no data dependencies, separate external rate limits (GSC quota vs. 4 AI-engine quotas vs. PostHog), and separate budget envelopes. Failure of any one pillar is isolated by the same try/except pattern Argus uses.

**Cross-agent data flow:**

```
Sage (triage)        ──┐
Echo (social)        ──┼→ Iris (themes)   ──→ Vega   (GEO prompt seeding)
Rex (competitive)    ──┼→ Selene (SEO gaps + LLM gap analysis)
                       └→ Cyra   (CRO hypothesis priors)

PostHog              ──→ Cyra   (funnel time-series)
GSC                  ──→ Selene (keyword performance)
4 AI engines + opt-5 ──→ Vega   (mention rate + citations + quality)

Selene/Vega/Cyra → analytics_recommendations table → next-cycle Mox briefs
```

All three agents are **pure auditors**. They read external systems and emit `Recommendation` rows. Mox/Kai/Pax pick up briefs from `.devrel/deliverables/{pillar}-brief-*.md` on the next cycle, exactly the way they already pick up Argus briefs. Zero new write integrations.

## 3. Per-agent specs

### 3.1 Selene — SEO Auditor

**Purpose:** identify content gaps, technical SEO regressions, and keyword opportunities on the user's product website. Emit recommendations Mox turns into blog/landing-page briefs.

**Purpose (expanded 2026-05-06):** position Selene as the **Multi-Surface Search auditor** — moving past blue-link rankings to measure brand visibility across traditional SERPs, AI Overviews / SGE, and standalone LLM citations. Selene runs an Infrastructure-First hierarchy: technical health is the prerequisite for semantic and generative visibility.

**Inputs:**

- Sitemap-driven crawl (`<product_url>/sitemap.xml` → async fetch + BeautifulSoup parse for `<title>`, `<meta>`, `<h1..h6>`, internal-link graph, schema.org JSON-LD typed inventory). Override: `[growth].seo_pages = [...]`.
- `robots.txt` + `<product_url>/llms.txt` parsing — beyond Googlebot, also recognize `OAI-SearchBot`, `Anthropic-User`, `PerplexityBot`, `ClaudeBot` user-agents and verify allow rules.
- Rex's competitor profiles (`SharedContext.rex_competitive`) for gap analysis.
- GSC keyword performance via `searchanalytics.query` (rolling 90-day CTR / position / impressions per page).
- **PageSpeed Insights API** (free, no auth required at our scale) for Core Web Vitals 2.0 — Interaction to Next Paint (INP) and Largest Contentful Paint (LCP) per top page. Cached 30 days; only re-fetches on content drift.
- **Cross-pillar read: Vega's `geo_visibility` table** for `citation_share` + `quality_score` per URL across the 4 engines. Selene does NOT re-call AI engines; it consumes Vega's measurements as a data source for its Multi-Surface report.

**Core algorithm:**

1. **Infrastructure crawl** → build `PageProfile` dataclass (`url`, `title_len`, `meta_len`, `h_counts`, `internal_links_count`, `external_links_count`, `word_count`, `has_schema`, `schema_types: list[str]`, `inp_ms: int | None`, `lcp_ms: int | None`, `redirect_chain_len: int`).
2. **Technical heuristics:** missing meta, duplicate H1s, title >60 chars, thin content (<200 words), redirect chain >3 hops, soft-404 detection (200 status + low content + canonical mismatch), `llms.txt` absent OR no AI-bot allowlist, INP >200ms, LCP >2.5s, typed schema gap (no `Organization`+`sameAs`, no `Product`/`FAQPage`/`Author` where applicable).
3. **Semantic + generative gap analysis:** per top-10 GSC-impressions page, Sonnet reads our content + 3 competitor pages on the same query and returns:
   - Missing topics + entities aligned with the Knowledge Graph (entity-first, not keyword-frequency framing)
   - Atomic-answer suggestion: 40-60-word extractable summary for top-of-page (advisory; Kai materializes)
   - Internal-link opportunities scoped to the topical cluster (semantic strengthening, not random)
   - Information-gain assessment: what unique insight our page offers vs. top-ranking competitors (drives `rewrite` confidence)
4. **GSC trend:** decay (position worsened ≥3 ranks vs. 30d-prior with stable impressions) / opportunity (position 5-15 with rising impressions ≥30%) flagging.
5. **Multi-Surface visibility (cross-pillar):** read `geo_visibility` rows for URLs in our sitemap; aggregate `citation_share` + `quality_score` per URL across all engines; flag URLs that ARE cited but score badly (rewrite candidates) and URLs that should be cited but aren't (visibility gaps).

**Recommendation outputs** (action × `target_kind`):

- `rewrite × url` — gap analysis hits, decay, low-quality citations from `geo_visibility`, missing atomic answer
- `amplify × keyword` — opportunity (GSC trend)
- `investigate × url` — any technical issue (meta/H1/redirect/INP/LCP/typed-schema gap/llms.txt missing). `source_ids[0]` carries the issue kind so the brief generator can specialize the recommendation copy.
- `retire × url` — zero-traffic + zero inbound links

**Operational standards** (reflected in implementation):

- **Reviewer-first:** Selene's findings are validated against a source-of-truth before persistence — technical signals come from PSI/GSC (authoritative); semantic findings ground on competitor pages Rex actually crawled (not just Sonnet hallucinations).
- **Progressive disclosure:** report markdown lists findings in revenue/impact order (top-impressions pages first), not crawl order.
- **Human-in-the-Loop:** Selene only writes Recommendations + briefs. Mox + a human approve creative strategy before publishing. The auditor/maker boundary stays.
- **Monthly monitoring loop:** weekly cycle is stricter than the recommended 30-day cadence; calibration reports (`devrel seo calibration`) measure whether earlier `amplify`/`rewrite` recs actually moved keyword position, hit rate vs. coin-flip.

**Success metrics tracked:**

- Traditional: per-keyword position trend (from `seo_keyword_metrics`), Core Web Vitals pass rate (from `seo_page_profiles`), technical issue burndown (from `analytics_recommendations` lifecycle).
- Generative (read from Vega's tables): citation rate per URL across engines, share-of-model-voice vs. competitors.

**Cost:** ~$0.40/cycle (10 LLM gap calls × $0.04). GSC + sitemap crawl + PSI API are free at this scale. Cross-pillar geo_visibility reads have zero cost (already-persisted data).

### 3.2 Vega — GEO (AI-search) Auditor

**Purpose:** measure brand visibility in AI search engines (mention rate, citation share, answer quality) over time; surface engines and queries where the brand is losing ground.

**Inputs:**

- 30 prompts at `.devrel/geo/prompts.txt`, seeded from Rex competitors + Iris pain-point themes, regenerated quarterly via `devrel geo refresh-prompts`.
- 4 engines: Perplexity (new client), ChatGPT (OpenAI Responses API w/ web-search tool), Claude (Anthropic Messages API w/ web-search tool), Brave AI Search (existing `tools/search_tools.py`).
- Optional 5th: Google AI Overviews via SerpAPI when `[geo].include_google_ai_overviews = true` (requires `SERPAPI_API_KEY`).

**Core algorithm:**

1. Run each prompt × each engine in parallel via `asyncio.gather`, with a per-engine semaphore (default 5 concurrent) to respect rate limits.
2. Parse each response for: brand mention (substring + alias matching from config), competitor mentions (Rex's list), cited source URLs (regex on `[1]` markers + explicit URLs + domain matching).
3. Per-response score: `position_score` 1-5 (1 = first mentioned, 5 = barely), `citation_share` (% of cited URLs pointing at our domain), `mention_type` ∈ {`recommended`, `compared`, `indirect`, `direct`, `none`}.
4. Quality scoring: when brand IS mentioned, second LLM call (Haiku, cheap) judges accuracy + helpfulness on a 5-point rubric.
5. Aggregate: `engine × prompt → mention_rate`, `engine → citation_share`, `engine × competitor → share_of_voice`.

**Recommendation outputs:**

- `double_down × brand_query` (high mention rate, growing — keep producing this content)
- `investigate × brand_query` (zero mentions across all engines for queries where competitors win)
- `rewrite × url` (cited URLs that score badly on quality — our doc is misleading the engine)
- `amplify × competitor` (competitor mentioned more than us in ≥2 consecutive cycles)

**Cost:** ~$2.40/cycle (30 prompts × 4 engines × ~$0.02 + 30 quality judgments × $0.001).

### 3.3 Cyra — CRO Auditor

**Purpose:** identify funnel drop-offs and produce LLM-generated A/B test hypotheses that Nova picks up for experimental design and Mox materializes as test variants.

**Inputs:**

- PostHog event series via existing `tools.api_client.PostHogClient`. Default funnel auto-detected from highest-volume `$pageview → custom-event` chains. Override: `[growth].cro_funnel = ["$pageview", "signup_started", "signup_completed", "first_value"]`.
- Page HTML (the same async crawler Selene uses, restricted to funnel pages).
- Optional priors from Iris (pain-point themes) + Sage (recurring user-reported friction) for hypothesis ranking.

**Core algorithm:**

1. Pull funnel conversion rates over rolling 7d / 30d / 90d windows from PostHog.
2. Drop-off ranking: identify the step with highest absolute drop + biggest week-over-week deterioration. Flag step changes ≥5 percentage points.
3. For the worst-drop step, Sonnet reads page HTML + drop-off rate + Iris/Sage priors and drafts **3 A/B hypotheses** scored on impact / confidence / effort (ICE) — same scoring shape as Nova's experiment-design output for pipeline compatibility.
4. Cohort splitting: when sample size allows (`>= [cro].min_sample_size`), break drop-off by `utm_source` + `device_type` to surface segment-specific issues.

**Recommendation outputs:**

- `retest × funnel_step` (with 3 ICE-scored hypothesis briefs in `source_ids_json`)
- `investigate × funnel_step` (drop-off without enough data for hypotheses)
- `double_down × funnel_step` (step recently improved; lock in via permanent variant)

**Cost:** ~$0.30/cycle (3-5 LLM hypothesis calls × $0.04 + cohort breakdowns).

## 4. Schema v5 migration

### 4.1 Extending `analytics_recommendations`

```sql
ALTER TABLE analytics_recommendations ADD COLUMN pillar TEXT NOT NULL DEFAULT 'argus';
ALTER TABLE analytics_recommendations ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'content_id';

UPDATE analytics_recommendations
   SET pillar = 'argus', target_kind = 'content_id'
 WHERE pillar IS NULL OR pillar = '';

CREATE INDEX IF NOT EXISTS idx_recs_pillar_period
    ON analytics_recommendations(pillar, first_seen_period DESC);

CREATE INDEX IF NOT EXISTS idx_recs_target
    ON analytics_recommendations(target_kind, target);
```

`pillar ∈ {argus, seo, geo, cro}`. `target_kind ∈ {content_id, url, keyword, funnel_step, brand_query, competitor}`. The tuple `(pillar, action, target, target_kind)` is the natural key for lifecycle tracking. Argus's existing `first_seen_period` / `applied_at` / stale-≥2w logic generalizes by adding `pillar = ?` to every query.

### 4.2 New per-pillar fact tables

```sql
-- Selene (SEO): time-series for decay/opportunity detection
CREATE TABLE seo_keyword_metrics (
    keyword TEXT NOT NULL, page_url TEXT NOT NULL, period_end TEXT NOT NULL,
    position REAL, ctr REAL, impressions INTEGER, clicks INTEGER,
    PRIMARY KEY (keyword, page_url, period_end)
);

CREATE TABLE seo_page_profiles (
    page_url TEXT NOT NULL, period_end TEXT NOT NULL,
    title_len INTEGER, meta_len INTEGER, h1_count INTEGER,
    word_count INTEGER, has_schema INTEGER, internal_links INTEGER,
    crawled_at TEXT NOT NULL,
    PRIMARY KEY (page_url, period_end)
);

-- Vega (GEO): per-engine signal time-series; raw responses live on FS
CREATE TABLE geo_visibility (
    prompt_id TEXT NOT NULL, engine TEXT NOT NULL, period_end TEXT NOT NULL,
    is_mentioned INTEGER, mention_type TEXT, position_score INTEGER,
    citation_share REAL, quality_score INTEGER,
    response_path TEXT,    -- relative to .devrel/geo/responses/
    PRIMARY KEY (prompt_id, engine, period_end)
);

-- Cyra (CRO): funnel time-series with per-segment breakdowns
CREATE TABLE cro_funnel_metrics (
    funnel_id TEXT NOT NULL, step_index INTEGER NOT NULL, period_end TEXT NOT NULL,
    conversion_rate REAL, sample_size INTEGER, segment_breakdown_json TEXT,
    PRIMARY KEY (funnel_id, step_index, period_end)
);
```

### 4.3 Raw blob storage

Big payloads stay on the filesystem. The DB stores pointers via `response_path` etc.

```
.devrel/geo/responses/{period_end}/{engine}/{prompt_id}.json
.devrel/seo/crawls/{period_end}/{url-slug}.html
.devrel/cro/funnels/{period_end}/{funnel_id}.json
```

All gitignored via the existing `.devrel/` line. Estimated growth: ~12MB/year for GEO at default 4 engines × 30 prompts × 52 cycles × ~2KB/response.

### 4.4 Calibration

`argus.calibrate_recommendations()` already scores `double_down` / `retire` against subsequent metric history. Adding `pillar = ?` filter generalizes it. Each pillar implements `_score_outcome(rec)` → `improved` | `unchanged` | `regressed`:

- SEO: did `position` improve for that keyword in subsequent `seo_keyword_metrics` rows?
- GEO: did `mention_rate` rise for that brand_query in subsequent `geo_visibility` rows?
- CRO: did `conversion_rate` rise for that funnel_step in subsequent `cro_funnel_metrics` rows?

Argus's per-action hit-rate + lift-vs-coin-flip math runs on top.

## 5. Config additions

### 5.1 `.devrel/config.toml`

```toml
[orchestration]
argus_in_run = true        # cheap (~$0.03/cycle); renamed from analytics_in_run
cro_in_run   = true        # cheap (~$0.30/cycle)
seo_in_run   = false       # opt-in (~$0.40/cycle + GSC quota)
geo_in_run   = false       # opt-in (~$2.40/cycle)

[growth]
seo_pages       = []       # explicit URLs override sitemap.xml
cro_funnel      = []       # explicit step list overrides PostHog top-traffic
cro_funnel_id   = ""       # human-readable name for the funnel
geo_competitors = []       # adds to Rex's auto-derived list

[seo]
crawl_delay_ms  = 1000
max_crawl_pages = 200
gsc_property    = ""       # populated by `devrel seo connect-gsc`

[geo]
engines                     = ["perplexity", "openai", "anthropic", "brave"]
include_google_ai_overviews = false   # enables SerpAPI engine #5
concurrent_engine_requests  = 5
quality_judge_model         = "claude-haiku-4-5"

[cro]
min_sample_size  = 500
hypothesis_count = 3
```

`analytics_in_run = true` from v0.2.4 deserialises as `argus_in_run = true` with a deprecation warning until v1.0.

### 5.2 Env vars

Net-new:

- `PERPLEXITY_API_KEY` — `pplx-...`
- `SERPAPI_API_KEY` — opt-in only when `[geo].include_google_ai_overviews = true`
- `GSC_OAUTH_CLIENT_ID` + `GSC_OAUTH_CLIENT_SECRET` — bundled in the package for the shared "devrel-origin" GCP project; users never set these. Stored in `core/oauth_constants.py` and overridable via env var only for self-hosting maintainers.

Existing (already wired): `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `BRAVE_API_KEY`, `POSTHOG_API_KEY`.

### 5.3 Dependencies (`pyproject.toml`)

```toml
[project.optional-dependencies]
seo = [
    "google-api-python-client>=2.150.0",
    "google-auth-oauthlib>=1.2.0",
    "google-auth-httplib2>=0.2.0",
    "beautifulsoup4>=4.12.0",
]
geo-google = [
    "google-search-results>=2.4.2",
]
growth = [
    "devrel-origin[seo]",
    # geo + cro have zero new deps; their AI clients reuse existing openai/anthropic/httpx
]

# existing in v0.2.4 (this spec extends `dev` to pull `[growth]` so contributor
# tests exercise the full pipeline, mirroring the v0.2.4 pattern of
# `devrel-origin[video]` being included in `dev`)
video = ["openai>=1.50.0", "playwright>=1.49.0", "pyautogui>=0.9.54"]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "respx>=0.20.2",
    "ruff>=0.1.0",
    "mypy>=1.5.0",
    "build>=1.0.0",
    "twine>=5.0.0",
    "devrel-origin[video,growth]",
]
```

End-user paths:

- `pip install devrel-origin` — base + Argus + Cyra (PostHog client is core)
- `pip install 'devrel-origin[growth]'` — adds Selene + Vega
- `pip install 'devrel-origin[growth,geo-google]'` — full set including SerpAPI

## 6. CLI surface

Per-pillar namespaces (matches the `connect-gsc` and `funnel`-inspector verbs that only make sense for one pillar) plus a thin `devrel growth` umbrella for cross-cutting questions.

```
devrel seo {report|history|diff|calibration|connect-gsc|crawl}
devrel geo {report|history|diff|calibration|refresh-prompts}
devrel cro {report|history|diff|calibration|funnel}
devrel argus {report|history|diff|calibration}    (renamed from analytics)
devrel growth {summary|diff}                       (cross-pillar only)
```

`devrel analytics ...` aliases through to `devrel argus ...` until v1.0 to avoid breaking existing scripts.

`report` / `history` / `diff` / `calibration` keep the same flags Argus already documents (`--since`, `--push`, `--push-on-partial`, `--format json`). The pillar-specific verbs:

- `devrel seo connect-gsc` — full OAuth flow (browser opens, localhost:8765 listens, refresh token stored at `.devrel/credentials/gsc.json`)
- `devrel seo crawl [--no-cache]` — manual crawl trigger, useful for debugging
- `devrel geo refresh-prompts` — regenerate `.devrel/geo/prompts.txt` from current Iris themes + Rex competitors
- `devrel cro funnel [--show-detected]` — inspector for the auto-detected funnel; surfaces what events got picked

## 7. New Python modules

```
src/devrel_origin/
├── core/
│   ├── selene.py    (~600 LOC)    — SEO auditor agent
│   ├── vega.py      (~700 LOC)    — GEO auditor agent
│   ├── cyra.py      (~500 LOC)    — CRO auditor agent
│   └── growth/                    — shared helpers
│       ├── __init__.py
│       ├── recommendations.py     — generalize Argus's _persist + lifecycle
│       └── target_kinds.py        — TargetKind enum + collision guards
├── tools/
│   ├── perplexity_client.py  (~150 LOC, httpx-based)
│   ├── gsc_client.py         (~300 LOC, google-api-python-client + OAuth flow)
│   ├── serpapi_client.py     (~120 LOC, only loaded when opt-in flag set)
│   ├── seo_crawler.py        (~250 LOC, async sitemap + page parser)
│   └── api_client.py         (extend PostHogClient with funnel_query method)
└── cli/
    ├── seo.py                (~400 LOC)
    ├── geo.py                (~350 LOC)
    ├── cro.py                (~300 LOC)
    ├── argus.py              (renamed from analytics)
    └── growth.py             (~200 LOC)
```

## 8. Build sequence

### Wave 0 — Foundation (2 days)

1. Schema v5 migration (idempotent ALTER + 3 new fact tables + indexes). Integration test against a real v4 db dump.
2. `core/growth/recommendations.py` — generalize Argus's `_persist`, lifecycle queries, calibration.
3. `core/growth/target_kinds.py` — `TargetKind` enum + collision-guard tests.
4. `cli/growth.py` — umbrella with `summary` + `diff` placeholders.
5. **Submit Google OAuth verification application** in parallel — long pole; start before any SEO code exists.

### Wave 1 — Cyra (CRO) — 3 days

Cheapest, all deps already wired. Proves the pillar pattern end-to-end before bigger pieces.

1. `core/cyra.py` — funnel auto-detect from PostHog event volume + override.
2. Drop-off ranking with WoW deterioration flagging at ≥5pp.
3. LLM hypothesis generation (Sonnet, ICE-scored, 3 per worst-drop step).
4. Cohort split when `sample_size ≥ min_sample_size`.
5. `cli/cro.py` — `report` + `history` + `diff` + `calibration` + `funnel`.
6. Tests: respx-mocked PostHog + Sonnet fixtures.

### Wave 2 — Vega (GEO) — 5 days

Validates multi-engine aggregation; biggest LLM cost.

1. `tools/perplexity_client.py` — net-new, httpx-based, `tenacity` retry, error taxonomy from `tools/api_client`.
2. Adapt OpenAI client for Responses API + web-search tool; adapt Anthropic client for Messages API + web-search tool. Feature-flagged so an engine can be disabled without breaking the pillar.
3. `core/vega.py` — engine orchestrator with `asyncio.gather` + per-engine semaphore + per-prompt result merging.
4. Mention parser (substring + alias from config + Rex competitor list). Citation extractor (URL regex + domain matching). Quality scorer (Haiku, 5-point rubric).
5. `cli/geo.py` — `report` + `history` + `diff` + `calibration` + `refresh-prompts`.
6. Tests: respx fixtures per engine; offline corpus of 5 canned responses per engine for assertion stability.

### Wave 3 — Selene (SEO) — 8 days

Highest implementation risk (GSC OAuth). Done last so the agent pattern is well-understood. Scope expanded 2026-05-06 with the Multi-Surface Search direction — adds llms.txt + AI-bot directives, PageSpeed Insights API for INP/LCP, typed-schema inventory, cross-pillar reads of Vega's `geo_visibility`, and a reframed gap-analysis LLM prompt focused on entity-mapping + atomic answers.

1. `tools/gsc_client.py` — full installed-app OAuth flow: `connect-gsc` opens browser, listens on `localhost:8765`, exchanges code, encrypted-stores refresh token at `.devrel/credentials/gsc.json`. `searchanalytics.query` wrapper with quota handling + 30-day rolling window.
2. `tools/psi_client.py` — net-new PageSpeed Insights API client (free tier, no auth). Returns INP + LCP + redirect-chain length per URL. Caches results 30 days in `.devrel/seo/psi-cache/`.
3. `tools/seo_crawler.py` — async sitemap walker with `crawl_delay_ms` + `max_crawl_pages` cap. BeautifulSoup parse → `PageProfile` (now with `schema_types`, `inp_ms`, `lcp_ms`, `redirect_chain_len`). Caches HTML to `.devrel/seo/crawls/`. Parses `robots.txt` recognizing `OAI-SearchBot`/`Anthropic-User`/`PerplexityBot`/`ClaudeBot` and `<product_url>/llms.txt`.
4. `core/selene.py` — heuristic checks (existing + INP, LCP, typed-schema, llms.txt, redirect-chain), LLM gap analysis with the new entity-mapping + atomic-answer + information-gain framing, decay/opportunity flagging from GSC trend, **cross-pillar Multi-Surface aggregation** that reads `geo_visibility` rows for our sitemap URLs.
5. `cli/seo.py` — `connect-gsc`, `crawl`, `report`, `history`, `diff`, `calibration`. The `report` markdown groups findings by Infrastructure → Semantic → Generative tiers (progressive disclosure by impact).
6. Tests: respx for GSC + PSI APIs; canned crawl HTML fixtures with embedded JSON-LD; cross-pillar test seeds `geo_visibility` rows for assertions.

### Wave 4 — Polish + Atlas integration (2 days)

1. Atlas Stage 5c wiring: per-pillar `*_in_run` gates, all three pillars run concurrently via `asyncio.gather` with per-pillar try/except so one failure doesn't abort the others.
2. Brief handoff: each pillar writes `.devrel/deliverables/{pillar}-brief-{period}-{action}-{target}.md` in the shared format Mox already consumes.
3. Cross-pillar `devrel growth summary` dashboard + `devrel growth diff` for week-over-week pillar movement.
4. CHANGELOG, README, `docs/` updates. New "Setting up GEO" page (env vars, prompt seeding) and "Setting up SEO" page (GSC OAuth walkthrough).
5. Smoke test: full `devrel run` with all four pillars enabled in a dev workspace; verify briefs land + Recommendations persist.

### Budget summary

| Wave | Days | Calendar |
|---|---:|---|
| 0 — Foundation | 2 | Week 1 (Mon-Tue) |
| 1 — Cyra | 3 | Week 1 (Wed-Fri) |
| 2 — Vega | 5 | Week 2 (Mon-Fri) |
| 3 — Selene | 8 | Week 3 (Mon-Wed Wk4) |
| 4 — Polish | 2 | Week 4 (Thu-Fri) |
| **Total** | **20 days** | **~4 weeks** |

Ship target: v0.3.0 ~2026-06-05 (if work starts Mon 2026-05-12). The +2 days vs. the original 18-day plan are the Multi-Surface Search additions to Wave 3 (PSI client + llms.txt + typed schema + cross-pillar aggregation).

## 9. Risk register

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| 1 | GSC OAuth verification slow (>4 weeks) | Med | Med | Submit in Wave 0; users use "Testing" mode (≤100 users) until verified; clear docs about consent screen warning |
| 2 | OpenAI/Anthropic web search APIs shift mid-build | Med | Low | Adapter layer per engine; feature flags to disable a broken engine without breaking the pillar |
| 3 | PostHog funnel auto-detect picks wrong events | High | Low | `[growth].cro_funnel` override; `devrel cro funnel` inspector |
| 4 | GEO prompt set drifts as brand evolves | High | Med | Quarterly auto-regen via `devrel geo refresh-prompts`; budget gate prevents stale-prompt cost spike |
| 5 | Cost runaway on GEO (~$120/yr per project) | Low | Med | Existing `[orchestration].weekly_usd_cap`; BudgetGate forces Haiku for quality judge |
| 6 | Schema v5 migration breaks existing user's v4 db | Low | High | Integration test against real v4 db dump; ALTER has safe DEFAULTs; rollback script published in CHANGELOG |
| 7 | Mox brief-format incompatibility | Low | Low | Shared `growth/recommendations.py` writes one canonical brief; Mox already proven against Argus's format |
| 8 | SEO crawler hammers user's site | Low | Med | Default `crawl_delay_ms=1000`, `max_crawl_pages=200`; honors `robots.txt` |

## 10. Cost summary

Per project, weekly cycles, USD:

| Pillar | LLM | Third-party API | Annual |
|---|---:|---:|---:|
| Argus | $0.03 | — | $1.56 |
| Cyra (CRO) | $0.30 | — | $15.60 |
| Vega (GEO) | $2.40 | — | $124.80 |
| Selene (SEO) | $0.40 | — | $20.80 |
| SerpAPI (opt-in) | — | $50/mo flat | $600 |
| **Default install** | $0.33 | $0 | **$17/yr** |
| **Full Growth pipeline** | $3.13 | $0 | **$163/yr** |
| **Full + SerpAPI** | $3.13 | $50/mo | **$763/yr** |

## 11. Out of scope for v0.3.0

- Backlink data (Ahrefs/Moz integration). Tier (d) of SEO; revisit in v0.4.0 if user demand surfaces.
- Session-replay parsing for CRO. Tier (c); requires PostHog session recording feature, separate API surface, large-blob storage.
- AI-engine tracking beyond the 4 baseline + opt-in 5th. You.com / Phind / DuckDuckGo Assist deferred until any of them shows >10% market share.
- Direct write integrations from auditors (e.g. Selene auto-creating PRs with title/meta fixes). The auditor/maker boundary is intentional; if write loops are wanted later, they belong in Mox or a new "fixer" agent.
- Multi-property support. One product = one set of properties (matches existing `.devrel/` per-cwd model).

## 12. Open questions / future work

- **GSC verification timing.** Application submitted in Wave 0; verification timeline is Google's. Spec assumes 4-6 weeks. If verification stalls, evaluate fallback to service-account install path as v0.3.1.
- **Engine adapter API stability.** OpenAI's web-search tool shipped Q4 2025; spec is pinned to that surface. If the API shifts before Wave 2 ships, Vega may temporarily run on 3 engines (Perplexity + Anthropic + Brave).
- **Cyra funnel auto-detection accuracy.** Heuristic ("highest-volume `$pageview → custom-event` chain") will get some funnels wrong on real PostHog projects. Concrete success criterion to validate during Wave 1 testing: on 5 sample PostHog dumps from open-source projects, auto-detected funnel matches what a human marketer would draw ≥70% of the time. Below that, override-first mode becomes the default.
- **Vega prompt regeneration cadence.** Quarterly is a placeholder; tune from calibration data once 6+ months of Vega history exists.

---

**Approval gate:** spec approved by Daria 2026-05-05. Next step: implementation plan via `superpowers:writing-plans`.
