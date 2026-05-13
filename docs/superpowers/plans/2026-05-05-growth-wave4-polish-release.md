# Growth Pipeline Wave 4 — Polish + Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Cyra/Vega/Selene into Atlas Stage 5c with proper failure isolation, polish the cross-pillar `devrel growth` umbrella, write end-user docs, bump to v0.3.0, and ship.

**Architecture:** Consolidates the per-pillar Atlas branches from Waves 1-3 into a single Stage 5c block using `asyncio.gather` with per-pillar try/except. Adds rich `devrel growth summary` and `growth diff` views. New end-user docs walk through GEO env vars + the GSC OAuth flow. Version bumps + CHANGELOG entry. Tag v0.3.0 publishes via the existing release.yml OIDC workflow.

**Tech Stack:** Python 3.12 async, Typer CLI, pytest, GitHub Actions OIDC release pipeline (proven on v0.2.4).

**Spec:** `docs/superpowers/specs/2026-05-05-growth-pipeline-design.md`
**Depends on:** Waves 0-3 all closed.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/devrel_origin/core/atlas.py` | Modify | Replace 3 sequential pillar branches with one `asyncio.gather` block |
| `src/devrel_origin/cli/growth.py` | Modify | Richer `summary` (per-pillar table) + `diff` (cross-pillar movement) |
| `pyproject.toml` | Modify | Version bump `0.2.4` → `0.3.0` |
| `CHANGELOG.md` | Modify | Add `## 0.3.0 — 2026-05-29` (or actual ship date) section |
| `README.md` | Modify | Add Growth pipeline mention; install paths for `[growth]` and `[geo-google]` extras |
| `docs/seo-setup.md` | Create | End-user walkthrough: `devrel seo connect-gsc`, GSC property setup, the unverified-app warning |
| `docs/geo-setup.md` | Create | End-user walkthrough: env vars, prompt seeding, engine list, cost expectations |
| `tests/test_atlas.py` | Modify | Replace per-pillar tests from W1/W2/W3 with one combined Stage 5c test |
| `tests/cli/test_growth_command.py` | Modify | Add tests for the rich summary + diff |

---

## Task 1: Consolidate Atlas Stage 5c into one `asyncio.gather` block

**Files:**
- Modify: `src/devrel_origin/core/atlas.py`
- Modify: `tests/test_atlas.py`

- [ ] **Step 1: Write the consolidated test**

Append to `tests/test_atlas.py`:

```python
@pytest.mark.asyncio
async def test_stage_5c_runs_all_three_pillars_concurrently(tmp_path, monkeypatch):
    """Stage 5c — all four pillars run via asyncio.gather; one failure
    doesn't abort the others."""
    from unittest.mock import AsyncMock, MagicMock, patch

    # Build minimal Atlas with three pillars enabled, mocked agents
    # Cyra succeeds, Vega fails, Selene succeeds — full suite still records
    # cyra + selene reports, vega gets {"error": ...}.
    # ... (test setup mirrors the per-pillar tests from Waves 1-3)
```

(Reuse the helper stubs that existed in the per-pillar tests; mock each agent's `execute` and assert all three were awaited.)

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_atlas.py::test_stage_5c_runs_all_three_pillars_concurrently -v --no-cov
```

Expected: AssertionError on missing concurrent execution.

- [ ] **Step 3: Refactor the three sequential pillar branches into one gather**

In `src/devrel_origin/core/atlas.py`, find the three blocks added in Waves 1-3:

```python
if self.config.orchestration.cro_in_run: ...
if self.config.orchestration.geo_in_run: ...
if self.config.orchestration.seo_in_run: ...
```

Replace with one consolidated Stage 5c:

```python
# Stage 5c — Growth pillars (Cyra/Vega/Selene). All run concurrently.
# Per-pillar try/except so one failure doesn't abort the others.
async def _safe_run(name: str, coro):
    try:
        return name, await coro
    except Exception as e:
        logger.warning(f"Atlas Stage 5c ({name}) failed: {e}")
        return name, {"error": str(e)}

stage_5c_tasks = []
if self.config.orchestration.cro_in_run:
    cyra = Cyra(
        posthog_client=self.posthog,
        llm_client=self.llm,
        db_path=self.project_paths.devrel_dir / "state.db",
    )
    stage_5c_tasks.append(_safe_run("cyra", cyra.execute(
        period_end=self.context.week_of,
        report_id=f"cro-{self.context.week_of}",
        page_html_by_url={},
        iris_themes=self._extract_iris_themes(),
        sage_friction=self._extract_sage_friction(),
        deliverables_dir=self.project_paths.devrel_dir / "deliverables",
    )))
if self.config.orchestration.geo_in_run:
    vega = self._build_vega()
    stage_5c_tasks.append(_safe_run("vega", vega.execute(
        period_end=self.context.week_of,
        report_id=f"geo-{self.context.week_of}",
        deliverables_dir=self.project_paths.devrel_dir / "deliverables",
    )))
if self.config.orchestration.seo_in_run:
    selene = self._build_selene()
    stage_5c_tasks.append(_safe_run("selene", selene.execute(
        period_end=self.context.week_of,
        report_id=f"seo-{self.context.week_of}",
        rex_competitive_html=self._extract_rex_competitive_html(),
        deliverables_dir=self.project_paths.devrel_dir / "deliverables",
    )))

if stage_5c_tasks:
    pillar_results = await asyncio.gather(*stage_5c_tasks)
    for name, result in pillar_results:
        if name == "cyra":
            self.context.cro_report = result if isinstance(result, dict) else result.__dict__
        elif name == "vega":
            self.context.geo_report = (
                result if isinstance(result, dict)
                else {"period_end": result.period_end, "n_recommendations": len(result.recommendations)}
            )
        elif name == "selene":
            self.context.seo_report = (
                result if isinstance(result, dict)
                else {"period_end": result.period_end, "n_recommendations": len(result.recommendations)}
            )
```

- [ ] **Step 4: Run the consolidated test + full suite**

```bash
pytest tests/test_atlas.py -v --no-cov
pytest tests/ -q --no-header
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devrel_origin/core/atlas.py tests/test_atlas.py
git commit -m "refactor(atlas): Stage 5c via asyncio.gather with per-pillar isolation"
```

---

## Task 2: Rich `devrel growth summary` (per-pillar table + counts)

**Files:**
- Modify: `src/devrel_origin/cli/growth.py`
- Modify: `tests/cli/test_growth_command.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_growth_command.py`:

```python
import sqlite3

from devrel_origin.project import state


def test_growth_summary_shows_per_pillar_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devrel_dir = tmp_path / ".devrel"
    devrel_dir.mkdir()
    (devrel_dir / "config.toml").write_text(
        'product_name = "T"\nproduct_url = "https://e.com"\n'
    )
    db = devrel_dir / "state.db"
    state.init_db(db)
    # Seed one rec per pillar
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO analytics_reports (id, period_end, generated_at, body_json) "
            "VALUES (?, ?, datetime('now'), '{}')",
            ("r1", "2026-04-01"),
        )
        for pillar, target_kind, target in [
            ("argus", "content_id", "doc-1"),
            ("seo", "url", "https://e.com/p"),
            ("geo", "brand_query", "best K8s tool"),
            ("cro", "funnel_step", "signup_completed"),
        ]:
            conn.execute(
                "INSERT INTO analytics_recommendations "
                "(report_id, action, target, source_ids_json, confidence, "
                " first_seen_period, pillar, target_kind) "
                "VALUES (?, ?, ?, '[]', 0.7, '2026-04-01', ?, ?)",
                ("r1", "investigate", target, pillar, target_kind),
            )
        conn.commit()

    runner = CliRunner()
    result = runner.invoke(app, ["growth", "summary"])
    assert result.exit_code == 0
    # Each pillar's open-rec count should appear
    for pillar in ("argus", "seo", "geo", "cro"):
        assert pillar in result.output.lower()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/cli/test_growth_command.py::test_growth_summary_shows_per_pillar_counts -v --no-cov
```

Expected: result depends on Wave 0's placeholder summary; confirm the table is rendered with per-pillar rows.

(If Wave 0's `summary` already does this — which it does — this test should pass on first run. The point of this task is verifying the integration works end-to-end with rows from all four pillars.)

- [ ] **Step 3: Confirm summary already handles four pillars**

The Wave 0 `cli/growth.py` already iterates over `Pillar` enum values. Confirm by reading the existing code; no edit needed unless the test reveals a gap.

- [ ] **Step 4: Run tests**

```bash
pytest tests/cli/test_growth_command.py -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit (only if any code changed)**

If you needed to tweak `cli/growth.py`:

```bash
git add src/devrel_origin/cli/growth.py tests/cli/test_growth_command.py
git commit -m "test: cross-pillar summary integration with all four pillars"
```

If no code change was needed (test passed against existing code):

```bash
git add tests/cli/test_growth_command.py
git commit -m "test: cross-pillar summary integration"
```

---

## Task 3: README — Growth pipeline mention + install paths

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current README**

```bash
head -80 README.md
```

- [ ] **Step 2: Add a Growth pipeline section**

Insert after the existing "Architecture" section in `README.md`:

```markdown
## Growth pipeline (v0.3.0+)

Three new auditor agents extend the post-publish slot beyond Argus's content
performance:

- **Selene** — SEO auditor. Sitemap-driven crawl + LLM gap analysis vs. competitors
  + Google Search Console keyword performance. ~$0.40/cycle.
- **Vega** — GEO (AI-search) auditor. Brand visibility across Perplexity, ChatGPT,
  Claude, and Brave AI. ~$2.40/cycle.
- **Cyra** — CRO auditor. PostHog funnel drop-off + LLM-generated A/B hypotheses.
  ~$0.30/cycle.

All three are pure auditors. They emit structured `Recommendation` rows that
Mox picks up as content/test briefs on the next cycle. CLI surface:

```bash
devrel seo {connect-gsc|crawl|report|history|diff|calibration}
devrel geo {report|history|diff|calibration|refresh-prompts}
devrel cro {report|history|diff|calibration|funnel}
devrel growth {summary|diff}    # cross-pillar
```

### Install paths

```bash
pip install devrel-origin                         # base + Argus + Cyra
pip install 'devrel-origin[growth]'               # adds Selene + Vega
pip install 'devrel-origin[growth,geo-google]'    # full set including SerpAPI
```

`[growth]` adds Google API client + BeautifulSoup. `[geo-google]` adds
SerpAPI for the optional 5th GEO engine (Google AI Overviews). Defaults
keep argus + cro running in `devrel run`; `seo` and `geo` are opt-in via
`[orchestration].seo_in_run = true` and `geo_in_run = true` in
`.devrel/config.toml`.

See `docs/seo-setup.md` and `docs/geo-setup.md` for one-time setup walkthroughs.
```

- [ ] **Step 3: Verify README still renders cleanly**

```bash
# If you have a markdown linter:
ruff check README.md  # ruff doesn't lint md, this is just a smoke
# Visual check:
head -100 README.md
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): Growth pipeline (Selene/Vega/Cyra) + install paths"
```

---

## Task 4: `docs/seo-setup.md` — end-user GSC walkthrough

**Files:**
- Create: `docs/seo-setup.md`

- [ ] **Step 1: Write the doc**

Create `docs/seo-setup.md`:

```markdown
# Setting up Selene (SEO auditor)

Selene needs three things to do its job:

1. A site whose **sitemap.xml** it can crawl
2. **Google Search Console** access for the site (read-only OAuth)
3. The `[growth]` install extra (`pip install 'devrel-origin[growth]'`)

Cost: ~$0.40 per weekly cycle (~$21/year). GSC API + sitemap crawls are free.

## 1. Confirm your sitemap is published

Selene fetches `<your-site>/sitemap.xml` by default. Verify it returns 200
and contains `<url><loc>...</loc></url>` entries. If your sitemap lives at
a non-standard path, add an override to `.devrel/config.toml`:

```toml
[seo]
sitemap_url = "https://example.com/custom-sitemap.xml"
crawl_delay_ms = 1000        # polite default; raise if your site is slow
max_crawl_pages = 200        # cap so a misconfigured sitemap can't run away
```

If your site has no sitemap, list pages explicitly:

```toml
[growth]
seo_pages = ["https://example.com/", "https://example.com/pricing", "https://example.com/docs"]
```

## 2. Connect Google Search Console

```bash
devrel seo connect-gsc
```

This opens your default browser to Google's consent screen. The first time you
connect, you may see a warning: **"Google hasn't verified this app."**

That's expected. The shared `devrel-origin` GCP project has been submitted
for Google verification, but the review queue takes 4-6 weeks. Until verified,
proceed via:

> **Advanced → Continue to devrel-origin**

Read-only access (`webmasters.readonly` scope) is the only permission requested.
A refresh token is stored at `.devrel/credentials/gsc.json` (mode 0600,
gitignored). Selene only ever calls `searchanalytics.query` — never mutating
calls.

After connecting, set the verified property URL:

```toml
[seo]
gsc_property = "https://example.com/"   # match the property as shown in Search Console
```

For domain properties (e.g. `sc-domain:example.com`), use that exact string.

## 3. Enable Selene in the weekly cycle

```toml
[orchestration]
seo_in_run = true
```

Or run it on demand:

```bash
devrel seo report
devrel seo crawl --no-cache  # debug crawl without persistence
devrel seo history "your top keyword"
devrel seo diff 2026-04-01 2026-04-08
devrel seo calibration       # how often did `amplify` recs actually move position?
```

## 4. What gets emitted

Per cycle, Selene writes:

- `seo_keyword_metrics` rows — keyword × page × period from GSC (`devrel seo history` reads these)
- `seo_page_profiles` rows — on-page signals per crawled URL
- `analytics_recommendations` rows — `pillar='seo'`, action ∈ {investigate, rewrite, amplify}
- `.devrel/deliverables/seo-brief-*.md` — Mox-ready briefs per recommendation

Mox picks up the briefs on its next cycle and turns them into blog post
or landing-page drafts.

## 5. Troubleshooting

- **"OAuth client not configured"** — the maintainer hasn't pasted the real
  GCP credentials into `core/oauth_constants.py` yet. See
  `docs/setup-google-oauth.md` (maintainer-only).
- **403 on `searchanalytics.query`** — your Google account doesn't have
  Search Console access for that property. Add the account at
  `https://search.google.com/search-console/users` (Owner or Full user).
- **Empty `keyword_opportunities`** — GSC needs at least 30 days of data for
  trend classification. New properties will produce a no-trend report on
  first run.
- **Crawler hammers your site** — raise `crawl_delay_ms`. Selene also honors
  `robots.txt` automatically.
```

- [ ] **Step 2: Commit**

```bash
git add docs/seo-setup.md
git commit -m "docs: end-user setup walkthrough for Selene (SEO + GSC OAuth)"
```

---

## Task 5: `docs/geo-setup.md` — end-user Vega walkthrough

**Files:**
- Create: `docs/geo-setup.md`

- [ ] **Step 1: Write the doc**

Create `docs/geo-setup.md`:

```markdown
# Setting up Vega (GEO auditor)

Vega measures your brand's visibility in AI search engines (Perplexity,
ChatGPT, Claude, Brave AI) by running a curated 30-prompt set against each.

Cost: ~$2.40 per weekly cycle (~$125/year at default 4 engines).

## 1. Set the API keys

```bash
# Required for the 4 default engines
export PERPLEXITY_API_KEY="pplx-..."
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export BRAVE_API_KEY="..."

# Optional — adds Google AI Overviews as a 5th engine
export SERPAPI_API_KEY="..."
```

Drop them in `.env` next to `.devrel/config.toml` (the loader picks both up).

## 2. Configure the engine list

```toml
[geo]
engines = ["perplexity", "openai", "anthropic", "brave"]  # default
include_google_ai_overviews = false   # set true + SERPAPI_API_KEY for 5th engine
concurrent_engine_requests = 5        # per-engine semaphore for rate limiting
quality_judge_model = "claude-haiku-4-5"
```

## 3. Seed the prompt set

Vega runs against `.devrel/geo/prompts.txt` (one prompt per line, `#`
lines skipped). Add prompts manually or seed from your competitive
landscape:

```bash
devrel geo refresh-prompts \
    --seed "best Kubernetes observability tool" \
    --seed "<your brand> vs Datadog" \
    --seed "what is <your brand>"
```

Recommended: 30 prompts mixing recommendation queries (`best <category>
tool`), comparison queries (`<your brand> vs <competitor>`), and
evaluation queries (`pros and cons of <your brand>`). Regenerate quarterly
as the brand evolves.

## 4. Configure brand + competitors

In `.devrel/config.toml`:

```toml
product_name = "OpenClaw"
product_domain = "openclaw.ai"           # used for citation_share computation
brand_aliases = ["OC", "Open Claw"]      # alternate forms Vega should treat as mentions

[growth]
geo_competitors = ["Datadog", "New Relic", "Grafana"]  # adds to Rex's auto-derived list
```

## 5. Enable Vega in the weekly cycle

```toml
[orchestration]
geo_in_run = true
```

Or run on demand:

```bash
devrel geo report
devrel geo history perplexity
devrel geo diff 2026-04-01 2026-04-08
devrel geo calibration
```

## 6. What gets emitted

Per cycle, Vega writes:

- `geo_visibility` rows — per prompt × per engine × period (`devrel geo history` reads these)
- Raw responses at `.devrel/geo/responses/{period}/{engine}/{prompt}.json`
- `analytics_recommendations` rows — `pillar='geo'`, action ∈ {investigate, double_down, amplify, rewrite}
- `.devrel/deliverables/geo-brief-*.md` — Mox-ready briefs per recommendation

## 7. Cost control

Each cycle = 30 prompts × 4 engines = 120 LLM calls + 30 quality-judge calls (Haiku).

If your weekly cap (`[orchestration].weekly_usd_cap`) trips, BudgetGate
forces Haiku for the quality judge (already does); GEO will continue to
emit but with cheaper quality scoring.

To reduce cost, drop engines from the `[geo].engines` list or shorten
`prompts.txt`. Quarterly auto-regen via `devrel geo refresh-prompts` keeps
the set lean as your brand language evolves.
```

- [ ] **Step 2: Commit**

```bash
git add docs/geo-setup.md
git commit -m "docs: end-user setup walkthrough for Vega (GEO + 4-engine config)"
```

---

## Task 6: CHANGELOG entry for v0.3.0

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Read the current top of CHANGELOG**

```bash
head -10 CHANGELOG.md
```

- [ ] **Step 2: Add the v0.3.0 entry above 0.2.4**

Insert after the `# Changelog` heading:

```markdown
## 0.3.0 — 2026-05-29

Growth pipeline launch. Three new auditor agents — Selene (SEO), Vega (GEO),
Cyra (CRO) — join Atlas as a fourth pipeline alongside Health, DevRel, Sales.
All three follow Argus's auditor pattern: gather signals, score
deterministically, emit structured `Recommendation` rows, stage Mox-ready
briefs.

### Added

- **Selene** — Multi-Surface Search auditor. Sitemap-driven crawl + on-page
  heuristic checks (missing meta, dup H1, thin content, typed-schema gaps,
  redirect chains) + Core Web Vitals 2.0 from PageSpeed Insights API
  (INP < 200ms, LCP < 2.5s thresholds) + `llms.txt` + AI-bot directive
  validation (OAI-SearchBot, Anthropic-User, PerplexityBot, ClaudeBot) +
  LLM gap analysis with entity-mapping + atomic-answer + information-gain
  framing vs. Rex's competitor pages + Google Search Console keyword
  performance via read-only OAuth + cross-pillar reads of Vega's
  `geo_visibility` for Multi-Surface citation/quality aggregation. CLI:
  `devrel seo {connect-gsc|crawl|report|history|diff|calibration}`.
- **Vega** — GEO auditor. Measures brand visibility (mention rate,
  citation share, answer quality) across 4 AI search engines: Perplexity,
  ChatGPT (OpenAI Responses API + web search), Claude (Messages API + web
  search), Brave AI. 30-prompt set seeded from Rex competitors + Iris
  themes, regeneratable via `devrel geo refresh-prompts`. Optional 5th
  engine: Google AI Overviews via SerpAPI when
  `[geo].include_google_ai_overviews = true`. CLI: `devrel geo
  {report|history|diff|calibration|refresh-prompts}`.
- **Cyra** — CRO auditor. Auto-detects funnels from PostHog event volume
  (override via `[growth].cro_funnel`), ranks drop-offs by absolute drop
  + WoW deterioration (≥5pp), generates 3 ICE-scored A/B hypotheses per
  worst-drop step using Sonnet, and breaks down by `utm_source × device_type`
  cohorts when sample size allows. CLI: `devrel cro
  {report|history|diff|calibration|funnel}`.
- **`devrel growth` umbrella** — cross-pillar `summary` (per-pillar open-rec
  counts) and `diff` (week-over-week pillar movement). Pillar-specific verbs
  live in `seo`/`geo`/`cro`/`argus`.
- **Schema v5** — extends `analytics_recommendations` with `pillar` (`argus`/
  `seo`/`geo`/`cro`) and `target_kind` (`content_id`/`url`/`keyword`/
  `funnel_step`/`brand_query`/`competitor`) columns. Adds three new fact tables:
  `seo_keyword_metrics` (GSC time-series), `seo_page_profiles`, `geo_visibility`,
  `cro_funnel_metrics`. Migration is idempotent; existing v4 rows backfill to
  `pillar='argus'`+`target_kind='content_id'`.
- **`core/growth/` shared module** — pillar-agnostic `Recommendation` dataclass
  with `(pillar, target_kind)` validator; `persist_recommendation`,
  `find_open_by_target`, `mark_applied`, `find_stale`, and `calibrate` helpers.
  Argus migrates to this module; v0.3.0 callers all share one persistence path.
- **Atlas Stage 5c** — post-publish parallel to Argus's 5b. All three Growth
  pillars run via `asyncio.gather` with per-pillar `try/except` for failure
  isolation. Per-pillar gates: `[orchestration].argus_in_run`/`cro_in_run` (default
  ON, cheap) and `seo_in_run`/`geo_in_run` (default OFF, opt-in heavies).
- **`[seo]` and `[geo-google]` optional install extras**. `pip install
  'devrel-origin[growth]'` adds Selene + Vega deps (Google API client + OAuth +
  BeautifulSoup). `[geo-google]` adds SerpAPI for the optional 5th GEO engine.

### Changed

- `analytics_in_run` config flag is renamed `argus_in_run`. The old name
  continues to work via a `__post_init__` alias and emits a DeprecationWarning;
  the alias will be removed in v1.0.
- `devrel analytics ...` CLI namespace is renamed to `devrel argus ...`. The
  old namespace is kept as a backward-compat alias with a deprecation warning,
  removed in v1.0.

### Documentation

- `docs/seo-setup.md` — end-user walkthrough for the GSC OAuth flow,
  sitemap setup, and reading the unverified-app warning.
- `docs/geo-setup.md` — end-user walkthrough for engine API keys, prompt
  seeding, brand + competitor config, and cost control.
- `docs/setup-google-oauth.md` (maintainer-only) — GCP project setup +
  OAuth verification submission.

### Internal

- 30+ new tests across `tests/core/growth/`, `tests/test_cyra.py`,
  `tests/test_vega.py`, `tests/test_selene.py`, plus per-engine + per-CLI
  tests. Suite is now ~920 pass / 21 xfail / coverage ≥75%.
- New deps: `google-api-python-client>=2.150.0`,
  `google-auth-oauthlib>=1.2.0`, `google-auth-httplib2>=0.2.0`,
  `beautifulsoup4>=4.12.0` (all in `[seo]` extra). Optional:
  `google-search-results>=2.4.2` (in `[geo-google]` extra).

```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): v0.3.0 — Growth pipeline (Selene/Vega/Cyra)"
```

---

## Task 7: Bump version to 0.3.0

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit `pyproject.toml`**

Change line `version = "0.2.4"` to `version = "0.3.0"`.

- [ ] **Step 2: Reinstall + verify version derivation**

```bash
cd ~/devrel-origin && source .venv/bin/activate
pip install -e ".[dev]" --quiet
python -c "from devrel_origin import __version__; print(__version__)"
# Expected: 0.3.0
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version 0.2.4 → 0.3.0"
```

---

## Task 8: Full pre-publish gate

**Files:** none — verification only.

- [ ] **Step 1: Lint + format**

```bash
ruff check . && ruff format --check . | tail -1
```

Expected: clean.

- [ ] **Step 2: Full test suite**

```bash
pytest tests/ -q --no-header
```

Expected: ~920 passed / 21 xfailed / coverage ≥75%.

- [ ] **Step 3: Build + twine check**

```bash
rm -rf dist/ build/
python -m build 2>&1 | tail -3
python -m twine check dist/* 2>&1 | tail -3
```

Expected: wheel + sdist build clean; twine PASSED for both.

- [ ] **Step 4: Fresh-venv smoke (no extras)**

```bash
rm -rf /tmp/devrel-novideo
python3.13 -m venv /tmp/devrel-novideo
source /tmp/devrel-novideo/bin/activate
pip install --quiet ~/devrel-origin/dist/devrel_origin-0.3.0-py3-none-any.whl
devrel --version
# Expected: devrel-origin 0.3.0
python -c "from devrel_origin.core.atlas import Atlas; from devrel_origin.cli import app; print('imports OK')"
# Expected: imports OK
```

- [ ] **Step 5: Fresh-venv smoke (`[growth]`)**

```bash
rm -rf /tmp/devrel-growth
python3.13 -m venv /tmp/devrel-growth
source /tmp/devrel-growth/bin/activate
pip install --quiet "$HOME/devrel-origin/dist/devrel_origin-0.3.0-py3-none-any.whl[growth]"
pip list 2>/dev/null | grep -iE "google-api|beautifulsoup"
# Expected: 2+ google-* + beautifulsoup4
devrel --version
# Expected: devrel-origin 0.3.0
```

If any step fails: stop and fix before tagging.

---

## Task 9: Tag v0.3.0

**Files:** none — git only.

- [ ] **Step 1: Verify git status is clean**

```bash
cd ~/devrel-origin
git status --short
git log --oneline -10
```

Expected: working tree clean; recent commits cover Wave 4 work.

- [ ] **Step 2: Push the branch**

```bash
git push origin main
```

- [ ] **Step 3: Create the annotated tag + push**

```bash
git tag -a v0.3.0 -m "$(cat <<'EOF'
v0.3.0

Growth pipeline launch. Selene (SEO), Vega (GEO), Cyra (CRO) — three new
auditor agents extending Atlas's post-publish slot beyond Argus's content
performance into organic search, AI-engine visibility, and conversion
funnel diagnosis.

Schema v5 extends analytics_recommendations for cross-pillar lifecycle
tracking. CLI: per-pillar namespaces (devrel seo/geo/cro/argus) plus a
devrel growth umbrella for cross-pillar views. New optional install
extras [seo] and [geo-google] keep the default install slim.

See CHANGELOG.md for full release notes.
EOF
)"
git push origin v0.3.0
```

- [ ] **Step 4: Watch the release workflow**

```bash
gh run list --workflow=release.yml --limit=1
gh run watch $(gh run list --workflow=release.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: 3 jobs (Build → Publish → GitHub release) all green.

- [ ] **Step 5: Verify on PyPI + install from PyPI**

```bash
sleep 10  # give PyPI's CDN a moment
curl -s https://pypi.org/pypi/devrel-origin/json | python3 -c \
  "import sys, json; d = json.load(sys.stdin); print(d['info']['version'])"
# Expected: 0.3.0

rm -rf /tmp/devrel-pypi
python3.13 -m venv /tmp/devrel-pypi
source /tmp/devrel-pypi/bin/activate
pip install --quiet devrel-origin
devrel --version
# Expected: devrel-origin 0.3.0
```

---

## Task 10: Post-release smoke + announcement

**Files:** optional release notes blog post / social post.

- [ ] **Step 1: Run a real `devrel run` cycle in a fresh project with all four pillars enabled**

(Manual smoke. In a project with `.devrel/` already configured + all relevant API keys + GSC connected:)

```toml
[orchestration]
argus_in_run = true
cro_in_run   = true
seo_in_run   = true
geo_in_run   = true
```

```bash
devrel run --week-of $(date -I)
```

Verify:
- [ ] All 4 pillars wrote rows to `analytics_recommendations`
- [ ] At least one brief per pillar landed in `.devrel/deliverables/`
- [ ] `devrel growth summary` shows non-zero open recs in all four rows
- [ ] No errors in the cycle log other than expected per-pillar failures (e.g. if Brave AI API is rate-limited, the cycle continues)
- [ ] Total cost reported by `devrel cost` is in the expected ~$3-4 range

- [ ] **Step 2: Update `gtm-labs.co/devrel-origin` landing**

The landing copy currently references "13 agents." Update to "16 agents"
and add a Growth pipeline section. Repo path:
`landing/index.html` (also lives at `~/gtm-labs/public/devrel-origin/index.html` per project memory).

- [ ] **Step 3: Update project memory**

Save the v0.3.0 ship to memory at `project_devrel_origin.md`:
- 4 commits per wave landed on origin/main
- Tagged + published on PyPI
- 4-pillar weekly cycle smoke clean

---

## Wave 4 closeout checklist

- [ ] `pytest tests/ -q --no-header` shows ~920 passed / 21 xfailed
- [ ] `ruff check .` and `ruff format --check .` both clean
- [ ] `python -m build` + `twine check` PASSED both wheel and sdist
- [ ] Fresh py3.13 venv install of `devrel-origin` (no extras) works
- [ ] Fresh py3.13 venv install of `devrel-origin[growth]` pulls Google + BS4
- [ ] `pyproject.toml` version is `0.3.0`
- [ ] `CHANGELOG.md` has the v0.3.0 section
- [ ] `README.md` mentions Growth pipeline and install paths
- [ ] `docs/seo-setup.md` and `docs/geo-setup.md` published
- [ ] `git tag v0.3.0` created and pushed
- [ ] Release workflow green; PyPI shows `0.3.0`
- [ ] `pip install devrel-origin` from a fresh venv installs `0.3.0`
- [ ] Manual `devrel run` smoke with all four pillars completes without errors
- [ ] Landing page (gtm-labs.co/devrel-origin) updated to "16 agents"

When all checked: v0.3.0 shipped. Growth pipeline is live.
