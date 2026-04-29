# AI DevRel + Sales Agent System

**12 autonomous AI agents that replace a full developer advocacy and sales team.**

This system handles the complete DevRel + Sales lifecycle — community triage, social listening, feedback synthesis, growth experimentation, content creation, video production, technical documentation, competitive intelligence, sales enablement, campaign marketing, system health monitoring, and brand consistency auditing — all coordinated through a single orchestrator with shared context, cross-run memory, and a self-improving content pipeline.

**Currently targeting [OpenClaw](https://openclaw.ai)** — a self-hosted, local-first personal AI assistant platform. Retargetable at any DevTools product by changing `product_name` in `config/agent_config.yaml` (or `PRODUCT_NAME` env var) and swapping the knowledge base.

> Every deliverable in this repository was produced by the agent system itself.

---

## What It Does

```
Pre-flight  Watchdog checks system health, integration status, budget usage

Monday      Sage scans GitHub → triages issues, flags churn risks, spots champions
            Echo scans Reddit/HN/Twitter → LLM-classified sentiment, engagement opportunities

Tuesday     Rex + Iris run in parallel:
              Rex → competitive landscape, threat assessment, opportunity mapping
              Iris → theme extraction, pain point ranking, developer journey mapping

Wednesday   Nova + Kai run in parallel:
              Nova → A/B experiments with power analysis, funnel optimization
              Kai → tutorials grounded in pain points, deduped against previous weeks

Thursday    Vox produces video → screen-recorded walkthroughs with TTS narration
            Dex generates docs → architecture overviews and API references from source code

Friday      Sentinel audits all content → brand voice, ICP alignment, messaging coherence
            Atlas compiles OKRs → archives context, publishes to Sheets, sends digest
```

One command runs the full pipeline:

```bash
python -m devrel_swarm.core.atlas --weekly-cycle
```

---

## The Agents

| Agent | Role | What It Produces |
|-------|------|-----------------|
| **Atlas** | Orchestrator | Coordinates all agents, manages shared context, retries with backoff, tracks OKRs, publishes & notifies |
| **Watchdog** | System Health | Pre-flight health checks, integration status, budget monitoring, stale output detection |
| **Sage** | Community Manager | Issue triage reports with priority scoring, sentiment analysis, churn risk flags, suggested responses |
| **Echo** | Social Listener | Cross-platform mention reports with LLM-classified sentiment, engagement opportunities, reputation risk alerts |
| **Iris** | Feedback Synthesizer | Theme extraction with chunked processing (no signal caps), developer journey maps, content opportunity lists |
| **Nova** | Growth Strategist | Pre-registered experiment designs with power analysis, funnel drop-off detection, cohort segmentation |
| **Kai** | Content Creator | Tutorials, blog posts, changelogs — revision-looped, deduped against previous weeks, code-validated |
| **Vox** | Video Producer | Screen-recorded tutorials with TTS narration, FFmpeg overlays, step-by-step walkthroughs |
| **Dex** | Documentation Generator | Architecture overviews, API references, module guides — parsed from source via AST |
| **Rex** | Competitive Intelligence | Parallel competitor discovery, threat assessment, opportunity mapping, Apollo enrichment |
| **Pax** | Sales Enablement | Outreach emails, battle cards, nurture sequences — revision-looped, grounded in Rex intel + Iris pain points |
| **Mox** | Campaign Marketing | Blog posts, landing pages, social batches, campaign briefs — revision-looped, parallel analytics |
| **Sentinel** | Brand Auditor | Post-pipeline brand voice audit, ICP alignment check, cross-piece consistency, quality scoring |

### Pipeline Architecture

```
Watchdog (health check)
    │
    ▼
Sage + Echo + Dex ──── parallel (no cross-dependencies)
    │
    ▼
Rex + Iris ──────────── parallel (both use Sage + Echo output)
    │
    ▼
Nova + Kai ──────────── parallel (both use Iris themes)
    │
    ▼
Vox (uses Kai content)
    │
    ▼
Sentinel (brand audit)
    │
    ▼
Instantly sync → OKRs → Sheets publish → Telegram + Email digest
```

Every downstream agent sees upstream insights. Kai doesn't guess what to write about — it writes about the pain points Iris extracted from the issues Sage triaged, deduped against the last 4 weeks of content history.

---

## Content Quality Pipeline

Every content agent (Kai, Mox, Pax) uses a **generate → critique → revise** loop:

1. **Draft** — Generate content grounded in KB + upstream context
2. **Critique** — LLM editorial review scores accuracy, clarity, voice, structure (1-10)
3. **Revise** — If score < 7 or high-severity issues found, revise and re-critique
4. **Validate** — Code blocks checked via `ast.parse()` (Python), delimiter balancing (JS), `json.loads()` (JSON)
5. **Audit** — Sentinel runs post-pipeline brand consistency check across all outputs

Revision trace (rounds, scores, remaining issues) is included in every output for transparency.

---

## Cross-Run Memory

The system maintains memory across weekly cycles:

- **WeeklyMemory** summaries extracted from the last 4 archived contexts
- **Content dedup** — Kai's prompt includes previous content titles to avoid repetition
- **Trend detection** — Recurring themes are flagged for deeper coverage
- **Context archive** — Full SharedContext saved as `context_YYYY-WNN.json` per cycle

---

## Integrations

| Integration | Module | Purpose |
|-------------|--------|---------|
| **Google Sheets** | `src/devrel_swarm/tools/sheets.py` | Content calendar — auto-publishes drafts for editorial review |
| **Telegram** | `src/devrel_swarm/tools/notifications.py` | Real-time alerts on pipeline completion |
| **Email** | `src/devrel_swarm/tools/notifications.py` | HTML daily/weekly digest reports |
| **Instantly** | `src/devrel_swarm/tools/instantly_client.py` | Cold email campaigns with parallel lead upload |
| **Apollo** | `src/devrel_swarm/tools/apollo_client.py` | Lead enrichment with firmographic data |
| **Firecrawl/Brave** | `src/devrel_swarm/tools/search_tools.py` | Web search with dual-provider fallback |
| **MCP** | `src/devrel_swarm/tools/mcp_server.py` | 14 tools for Claude Desktop, Cursor, Windsurf |

All integrations degrade gracefully — if env vars aren't set, the step is skipped.

---

## Quick Start

```bash
git clone https://github.com/dovzhikova/devrel-swarm.git
cd devrel-swarm

pip install -r requirements.txt

cp config/env.example .env
# Required: ANTHROPIC_API_KEY
# Optional: GITHUB_TOKEN, FIRECRAWL_API_KEY, INSTANTLY_API_KEY, APOLLO_API_KEY
# Notifications: TELEGRAM_BOT_TOKEN, EMAIL_SENDER, SHEETS_SPREADSHEET_ID

# Run the full weekly pipeline
python -m devrel_swarm.core.atlas --weekly-cycle

# Run a single agent
python -m devrel_swarm.core.atlas --agent kai --task "Write a tutorial on feature flags"
python -m devrel_swarm.core.atlas --agent sentinel --task "Audit this week's content"
python -m devrel_swarm.core.atlas --agent watchdog --task "Check system health"

# Auto-populate knowledge base from public content
python -m devrel_swarm.tools.kb_harvester --url "https://example.com/docs" --category docs

# Install cron schedule
python -m devrel_swarm.tools.scheduler --action install

# Send digest manually
python -m devrel_swarm.tools.scheduler --action digest --mode weekly

# Run tests
pytest tests/ -v
```

### Environment Variables

| Variable | Required | Used By |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | All LLM-powered agents |
| `GITHUB_TOKEN` | For issue triage | Sage |
| `FIRECRAWL_API_KEY` | For web search | Echo, Kai, Rex, KB Harvester |
| `INSTANTLY_API_KEY` | For email campaigns | Pax, Mox |
| `APOLLO_API_KEY` | For lead enrichment | Rex, Pax |
| `TELEGRAM_BOT_TOKEN` | For notifications | Atlas pipeline |
| `EMAIL_SENDER` / `EMAIL_PASSWORD` | For email digests | Atlas pipeline |
| `SHEETS_SPREADSHEET_ID` | For content calendar | Atlas pipeline |
| `OPENAI_API_KEY` | For TTS narration | Vox |

---

## Project Structure

```
src/devrel_swarm/core/
  atlas.py           Orchestrator — delegation, retry, SharedContext, OKR tracking,
                     cross-run memory, publish & notify
  watchdog.py        System Health — pre-flight checks, budget monitoring, integration status
  sage.py            Community Manager — issue triage, sentiment, churn risk
  echo.py            Social Listener — Reddit/HN/Twitter, LLM batch sentiment
  iris.py            Feedback Synthesizer — chunked theme extraction, journey mapping
  nova.py            Growth Strategist — experiments, funnels, power analysis
  kai.py             Content Creator — revision-looped tutorials, content dedup
  vox.py             Video Producer — screen recording, TTS, FFmpeg assembly
  dex.py             Documentation Generator — AST parsing, architecture docs
  rex.py             Competitive Intelligence — parallel search, Apollo enrichment
  pax.py             Sales Enablement — revision-looped outreach, battle cards
  mox.py             Campaign Marketing — revision-looped content, parallel analytics
  sentinel.py        Brand Auditor — voice, ICP, messaging, quality scoring
  base.py            Shared utilities — TF-IDF KB search, prompt file loading
  llm.py             LLM client — generate, critique, revision loop, per-agent cost tracking
  agent_config.py    YAML config loader with product_name centralization
  video/             Vox sub-modules (script parser, TTS, recorder, overlays, assembler)

src/devrel_swarm/tools/
  api_client.py      Async PostHog API v2 client with typed DTOs
  github_tools.py    Async GitHub client (issues, comments, profiles, labels)
  search_tools.py    Web search (Firecrawl + Brave), official docs via GitMCP
  code_validator.py  Syntax validation for code blocks in generated content
  notifications.py   Telegram + email delivery for digests and alerts
  sheets.py          Google Sheets content calendar publisher
  scheduler.py       Cron-based pipeline scheduling with CLI
  kb_harvester.py    Auto-populate knowledge base from public content
  instantly_client.py  Instantly AI client with parallel bulk lead upload
  apollo_client.py   Apollo.io client for lead enrichment
  mcp_server.py      MCP server exposing 14 tools via JSON-RPC

knowledge_base/      Curated product docs (auto-harvestable via kb_harvester)
optimize/            Per-agent prompt optimization files
tests/               Test suite (pytest + pytest-asyncio + respx)
config/              Environment template + agent configuration YAML
deliverables/        Agent-generated output artifacts
context_archive/     Weekly SharedContext JSON snapshots
```

---

## Retargeting to Another Product

The system is product-agnostic. To point it at a different product:

1. **Set `product_name`** in `config/agent_config.yaml` — flows to Rex, Pax, Mox automatically
2. **Harvest new KB** — `python -m devrel_swarm.tools.kb_harvester --url "https://newproduct.com/docs" --category docs`
3. **Update `src/devrel_swarm/tools/github_tools.py`** — Change the `OWNER/REPO` constants
4. **Optionally customize prompts** — Drop files into `optimize/{agent}/system_prompt.txt`
5. **Run** — `python -m devrel_swarm.core.atlas --weekly-cycle`

Works with: Supabase, Cal.com, Trigger.dev, Langfuse, Neon, Tinybird, or any product with a GitHub repo and docs.

---

## Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.12+ (async/await, dataclasses, type hints) |
| Agent SDK | Claude Agent SDK |
| Tool protocol | Model Context Protocol (MCP) |
| HTTP | httpx (async) |
| Model | Claude Sonnet 4.6 |
| Statistics | scipy (power analysis, Bayesian evaluation) |
| Video | Playwright (recording) + FFmpeg (assembly) + OpenAI TTS |
| Testing | pytest + pytest-asyncio + respx |

---

## Author

**Daria Dovzhikova** — DevTools Growth Strategist & AI Agent Builder
- 12+ years in DevTools (JetBrains, Huawei, Lightrun, Odigos)
- [dariadovzhikova.com](https://dariadovzhikova.com)

---

MIT License
