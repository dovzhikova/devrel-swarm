# devrel-swarm CLI — Phase 5: Migration + Docs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out the SaaS-direction work cleanly (tag-archive `product/v0-agentic-alpha`, remove its worktree), rewrite `README.md` to lead with the CLI surface (currently still framed around the agent-cycle narrative), and add a state-of-the-repo summary commit. After Phase 5, `main` reflects the shipped product end-to-end and there are no dangling branches or worktrees.

**Architecture:** Pure housekeeping. No new code, no test changes. The plan is three short tasks: archive, rewrite, summarize.

**Tech Stack:** git tagging + worktree management; markdown rewrite.

**Spec:** `docs/superpowers/specs/2026-04-29-devrel-swarm-cli-design.md` §"Migration path"
**Phases 1-4 (prerequisites, all merged):** `be971bd`, `121187e`, `bfb3bb5`, `86c2747` on `main`.

---

## File structure after Phase 5

No new files. Two file rewrites:
- `README.md` — leads with CLI install + first-run; relegates the agent-cycle narrative to a "How it works internally" section.
- `CLAUDE.md` — minor edits to ensure the "Identity" + first paragraphs reflect the CLI direction.

Plus removed:
- `.worktrees/v0-agentic-alpha/` (filesystem cleanup)
- `product/v0-agentic-alpha` (local branch, after tagging)

Plus added:
- `archive/v0-agentic-alpha` git tag pointing at the v0 branch's HEAD, preserving history.

---

## Pre-flight: confirm clean main

- [ ] **Step 1: Verify `main` is at Phase 4 head and worktree-clean**

```bash
cd /Users/macmini/devrel-swarm
git rev-parse --abbrev-ref HEAD
git log --oneline -3
git status --short
git worktree list
```
Expected: branch `main`, HEAD at `86c2747` (or later if the README rewrite below has landed), `git status` clean, `git worktree list` shows main + `.worktrees/v0-agentic-alpha`.

- [ ] **Step 2: Confirm baseline test suite still at 707/22**

```bash
.venv/bin/python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```
(Use the venv from the most recent worktree's `.venv` if a top-level one isn't present, or set up a fresh one.)

Expected: `707 passed, 22 failed`.

---

## Task 1: Archive `product/v0-agentic-alpha`

This branch was the SaaS direction. The CLI direction superseded it in Phase 1's spec commit. The cost-sink hook was already salvaged into Phase 4. The remaining commits (HTTP bridge, BudgetGate, SQLite storage from v0) are dead code in the CLI world. We tag them so history isn't lost, then remove the live branch and worktree.

**Files:** None (git plumbing).

- [ ] **Step 1: Inspect the v0 worktree state**

```bash
cd .worktrees/v0-agentic-alpha
git log --oneline -10
git status --short
```

There are uncommitted staged changes (`tests/test_http_bridge.py`, `tools/http_bridge.py`, `tools/storage.py`) — the A.4 hardening fold-in that was never committed. These were security improvements (path-traversal guard, fail-closed auth, queued-state job init, constant-time bearer compare) for the FastAPI HTTP bridge.

The HTTP bridge is dead code in the CLI direction. The hardening doesn't apply anywhere reachable. **Discard the uncommitted changes** rather than committing them — committing dead code into the archive is noise, not value.

- [ ] **Step 2: Discard the uncommitted staged changes inside the v0 worktree**

```bash
cd .worktrees/v0-agentic-alpha
git restore --staged --worktree tests/test_http_bridge.py tools/http_bridge.py tools/storage.py
git status --short
```
Expected: empty `git status` output. The branch HEAD stays at `e378bbc` — only the staged changes are gone.

- [ ] **Step 3: Return to main, create the archive tag**

```bash
cd /Users/macmini/devrel-swarm
git tag archive/v0-agentic-alpha product/v0-agentic-alpha -m "Archive: SaaS direction (per-instance Fly + Next.js central app), superseded by CLI direction in 2026-04-29 spec. Phase A code (SQLite storage, BudgetGate, HTTP bridge) preserved for reference."
git tag --list 'archive/*'
```
Expected: the new tag listed.

- [ ] **Step 4: Remove the worktree, then delete the branch**

```bash
git worktree remove .worktrees/v0-agentic-alpha
git branch -D product/v0-agentic-alpha
git worktree list
git branch -a | grep -E "(v0|product)" || echo "no v0/product branches"
```
Expected: `worktree list` shows only `main`. The `git branch -D` removes the local branch (force, because tagging it doesn't make it "merged" in git's eyes); the tag preserves the history.

(If `git branch -D` warns "not fully merged" — that's expected and correct here, since v0 was never merged. The archive tag is what preserves the history.)

- [ ] **Step 5: Verify the tag still resolves to the right commit**

```bash
git log archive/v0-agentic-alpha --oneline -3
```
Expected: shows `e378bbc` (the HTTP bridge commit) and the two below it.

- [ ] **Step 6: Commit (just the tag — no file changes)**

Tags don't need a commit. But for discoverability, add a short note to the spec's migration section pointing at the tag.

In `docs/superpowers/specs/2026-04-29-devrel-swarm-cli-design.md`, find the §"Migration from current state" subsection (it's near the bottom). Locate the bullet:
```
1. Tag-and-archive the SaaS branch: `git tag archive/v0-agentic-alpha product/v0-agentic-alpha`, then leave the branch in place but stop building on it.
```
Replace with:
```
1. ✅ Tag-and-archive the SaaS branch (done in Phase 5 as `archive/v0-agentic-alpha`; local `product/v0-agentic-alpha` branch removed). View the archived history with `git log archive/v0-agentic-alpha`.
```

- [ ] **Step 7: Commit the spec annotation**

```bash
git add docs/superpowers/specs/2026-04-29-devrel-swarm-cli-design.md
git commit -m "docs(spec): mark v0-agentic-alpha archive done (Phase 5)"
```

---

## Task 2: Rewrite `README.md` to lead with the CLI

The current `README.md` opens with **"12 autonomous AI agents that replace a full developer advocacy and sales team"** and walks through the agent-cycle narrative. That framing made sense in v0 (and lingered through Phases 1-4 because we didn't touch user-facing docs much). For the CLI product, the front door is `pipx install devrel-swarm` and `devrel init`. The agents are an implementation detail behind verbs.

The rewrite leads with install + first run; the agent narrative drops to a later section.

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: Read the current README**

```bash
wc -l README.md
sed -n '1,30p' README.md
```

- [ ] **Step 2: Replace `README.md` with the rewrite below**

Use the Write tool to replace the file entirely. The new content:

```markdown
# devrel-swarm

**A developer-first CLI for AI-powered DevRel, sales, and marketing.**

`devrel-swarm` is a `pipx`-installable command-line tool that runs a 12-agent system against any project — community triage, social listening, theme extraction, growth experiments, content production, video tutorials, documentation, competitive intel, sales outreach, and brand-consistent campaigns. Operates on a project repo the way `git`, `npm`, and `cargo` do.

Every piece of content the system produces flows through an 8-stage editorial pipeline (developmental edit → line edit → copy edit → anti-slop → reader-persona test → readability check → brand audit) so output reads like senior-editor work, not generic AI prose.

> Every deliverable in this repository was produced by the agent system itself.

---

## Quick start

```bash
pipx install devrel-swarm

cd /path/to/your/project
devrel init --name myproject --url https://myproject.dev --github-repo me/myproject

# edit .devrel/voice.md, .devrel/style.md, .devrel/slop-blocklist.md

export ANTHROPIC_API_KEY=sk-ant-...
devrel doctor                                  # check env + scaffold
devrel content draft "tutorial on feature flags" --type tutorial
devrel run                                     # full weekly pipeline
```

After `devrel init`, your repo has a `.devrel/` directory with:

```
.devrel/
  config.toml          # product identity, model selection, budget caps
  voice.md             # tone profile + sample passages    (commit)
  style.md             # house style + per-content targets (commit)
  slop-blocklist.md    # banned phrases                    (commit)
  kb/                  # knowledge base, TF-IDF indexed
  deliverables/        # generated outputs
  state.db             # SQLite: jobs, costs, checkpoints
```

The four committed files (`config.toml`, `voice.md`, `style.md`, `slop-blocklist.md`) encode the editorial contract. Diff them like any other source.

---

## Commands

```
# Bootstrap & health
devrel init                      bootstrap .devrel/ in cwd
devrel doctor [--json]           check env, API keys, KB freshness
devrel cost [--month YYYY-MM]    token + USD report from state.db

# Pipelines
devrel run                       full weekly cycle
devrel run --health              health check only (Watchdog)
devrel run --agent NAME --task T run a single agent ad-hoc

# DevRel
devrel triage [--days N]         GitHub issue triage (Sage)
devrel listen [--platforms ...]  Reddit / HN / X (Echo)
devrel synthesize                theme extraction (Iris)
devrel experiment HYPOTHESIS     A/B + power analysis (Nova)

# Content
devrel content draft PROMPT      revision-looped + 5-lever quality (Kai)
devrel content audit FILE        run quality pipeline on existing draft
devrel content slop FILE         run only the anti-slop pass
devrel docs build                AST-based docs (Dex)
devrel video record SCRIPT       screen-recorded tutorial (Vox)

# Sales
devrel intel COMPETITOR
devrel sales outreach COMPANY
devrel sales battlecard COMPETITOR
devrel sales sequence CAMPAIGN

# Marketing
devrel marketing blog TOPIC
devrel marketing landing TOPIC
devrel marketing social TOPIC
devrel marketing campaign BRIEF

# Knowledge base
devrel kb add URL [--category C]
devrel kb list
devrel kb refresh

# Config & schedule
devrel config get KEY
devrel config set KEY VALUE
devrel schedule install | list | remove

# Outputs
devrel deliverables list
devrel deliverables show NAME
```

Global flags on most verbs: `--json` (machine-readable output) and `--quiet`.

---

## Editorial quality pipeline

Every content-producing run (`devrel content draft`, `devrel content audit`, plus internal calls from `marketing`, `sales`, `kb`-driven tutorials) flows through 8 stages:

```
1. Generate          KB-grounded; voice.md + style.md in prompt
2. Developmental     critique+revise (structure, argument, hook)
3. Line edit         critique+revise (rhythm, voice fidelity)
4. Copy edit         critique+revise (grammar, code, consistency)
5. Anti-slop         regex blocklist + LLM lint; force-rewrite on hit;
                       second failure aborts loud with a phrase report
6. Reader persona    "skeptical senior backend dev" scores 1-10
7. Readability       Flesch + sentence variance + jargon density
                       checked against per-content-type targets
8. Brand audit       Sentinel (existing 6-dimension audit)

→ deliverables/ + revision-trace.json (every stage's score + diff)
```

Stages 5-7 use Haiku for cost; stages 2-4 use Sonnet. Total cost ≈ 2.5-4× a single revision loop, with prompt caching pulling toward the lower bound. BudgetGate guardrails (configurable in `.devrel/config.toml`) track spend; `devrel cost --month YYYY-MM` reports it.

---

## How it works internally

Hub-and-spoke with 12 agents. Atlas orchestrates; specialists execute across three pipelines.

```
Atlas (Orchestrator)
├── Health: Watchdog (pre-flight) + Sentinel (post-pipeline brand audit)
├── DevRel: Sage, Echo, Iris, Nova, Kai, Vox, Dex
└── Sales:  Rex, Pax, Mox
```

The weekly cycle (driven by `devrel run`):

```
Stage 0: Watchdog         (health + budget check)
Stage 1: Sage + Echo + Dex     parallel
Stage 2: Rex + Iris            parallel
Stage 3: Nova + Kai            parallel (Kai routes through quality pipeline)
Stage 4: Vox
Stage 5: Sentinel              brand audit
Stage 6: Instantly sync, OKR compilation, Sheets publish, digest
```

The `Atlas.delegate()` API also dispatches single-agent tasks, which is what every non-`run` verb wraps. So `devrel triage` is `Atlas.delegate("sage", "Triage GitHub issues from the last 7 days")` — the agents never appear in the public CLI surface, only the verbs.

---

## Configuration

`.devrel/config.toml` example:

```toml
[project]
name = "openclaw"
url = "https://openclaw.ai"
github_repo = "openclaw/openclaw"

[model]
default = "claude-sonnet-4-6"
cheap = "claude-haiku-4-5-20251001"
opus_opt_in = true

[budget]
monthly_usd = 100.0
warn_at_pct = 80
```

Edit with `devrel config set <key> <value>` or directly in your editor.

### Environment variables

| Variable | Required | Used by |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | yes | every LLM-using verb |
| `GITHUB_TOKEN` | for triage | Sage |
| `FIRECRAWL_API_KEY` | for KB harvest + intel | `kb add`, Echo, Rex |
| `BRAVE_API_KEY` | optional fallback | search |
| `INSTANTLY_API_KEY` | for cold-email sync | Pax, Mox |
| `APOLLO_API_KEY` | for lead enrichment | Rex, Pax |
| `TELEGRAM_BOT_TOKEN` | for digests | Atlas pipeline |
| `EMAIL_SENDER` / `EMAIL_PASSWORD` | for digests | Atlas pipeline |
| `OPENAI_API_KEY` | for video TTS | Vox |

`.env` files at the project root are loaded automatically. Cross-project shared keys can live at `~/.devrel/secrets.env`.

---

## Retargeting to another product

```bash
cd /path/to/other-project
devrel init --name otherproduct --url https://otherproduct.dev --github-repo owner/otherproduct
devrel kb add https://otherproduct.dev/docs --category docs
# edit voice.md / style.md / slop-blocklist.md to match the other product's voice
devrel doctor
devrel run
```

The agent system is product-agnostic. Per-project config + KB + voice files do all the targeting.

---

## Tech stack

| Component | Choice |
|---|---|
| Language | Python 3.12+ |
| CLI framework | Typer + Rich |
| Agent SDK | Claude Agent SDK |
| HTTP | httpx (async) |
| Default model | Claude Sonnet 4.6 (Haiku for cheap quality stages, Opus opt-in) |
| Stats | scipy (power analysis, Bayesian eval) |
| Video | Playwright + FFmpeg + OpenAI TTS |
| Storage | SQLite per project (.devrel/state.db) |
| Tests | pytest + pytest-asyncio + respx |

---

## Author

**Daria Dovzhikova** — DevTools Growth Strategist & AI Agent Builder
- 12+ years in DevTools (JetBrains, Huawei, Lightrun, Odigos)
- [dariadovzhikova.com](https://dariadovzhikova.com)

---

MIT License
```

- [ ] **Step 3: Verify the rewrite renders cleanly**

```bash
wc -l README.md
head -30 README.md
```
Expected: ~250 lines (similar to before), title now `devrel-swarm`, opens with the CLI framing.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(README): lead with CLI install + commands; relegate agent narrative"
```

---

## Task 3: Light cleanup of `CLAUDE.md`

`CLAUDE.md` is mostly accurate already (Phases 1-4 added entries) but the **Identity** opening still calls the project a "multi-agent developer advocacy and sales system" without leading with the CLI. Quick edit.

**Files:**
- Modify: `CLAUDE.md` (lines 1-15 only)

- [ ] **Step 1: Update the Identity section**

In `CLAUDE.md`, find:
```
## Identity

This is a **multi-agent developer advocacy and sales system**. 12 specialized AI agents replace a full DevRel + Sales team — community management, social listening, feedback synthesis, growth experimentation, content creation, video production, documentation generation, competitive intelligence, sales enablement, campaign marketing, system health monitoring, and brand consistency auditing — for any open-source DevTools product.

The system is retargetable by changing `product_name` in `config/agent_config.yaml` (or `PRODUCT_NAME` env var) and swapping the knowledge base.
```

Replace with:
```
## Identity

This is **`devrel-swarm`**, a `pipx`-installable Python CLI that runs a 12-agent DevRel + Sales + Marketing system against any project repo. Operates on `cwd` like `git` / `npm` — `devrel init` scaffolds a `.devrel/` directory with config, voice/style/slop files, knowledge base, and state DB. Every CLI verb (`devrel run`, `devrel content draft`, `devrel triage`, etc.) wraps a single-agent or pipeline call.

Every piece of content flows through an 8-stage editorial quality pipeline (`quality.editorial.run_pipeline`) before being shipped: developmental edit → line edit → copy edit → anti-slop → reader-persona → readability → brand audit.

The system is retargetable per project: each `.devrel/config.toml` carries the product identity (name, URL, github_repo), and `.devrel/kb/` carries the harvested docs.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): lead Identity with CLI framing"
```

---

## Task 4: State-of-the-repo summary

Final commit that documents the shipped surface for anyone discovering the repo. Just adds a `CHANGELOG.md` (a fresh file — none exists today) summarizing v0.2.0.

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write `CHANGELOG.md`**

```markdown
# Changelog

## 0.2.0 — 2026-04-29

The CLI direction. `devrel-swarm` is now a `pipx`-installable Python CLI that operates on a project repo (`.devrel/` per project, like `git`/`npm`/`cargo`).

### Added

- **CLI surface (18 verbs)**: `init`, `doctor`, `run`, `content {draft,audit,slop}`, `triage`, `listen`, `synthesize`, `experiment`, `intel`, `sales {outreach,battlecard,sequence}`, `marketing {blog,landing,social,campaign}`, `kb {add,list,refresh}`, `schedule {install,list,remove}`, `cost`, `deliverables {list,show}`, `config {get,set}`, `docs build`, `video record`.
- **8-stage editorial quality pipeline** (`quality.editorial.run_pipeline`): developmental → line → copy edit → anti-slop → reader-persona → readability → brand audit. Used by Kai, Mox, Pax for every content output.
- **Project bootstrap** (`devrel init`): `.devrel/` scaffold with `config.toml`, `voice.md`, `style.md`, `slop-blocklist.md`, `kb/`, `deliverables/`, `state.db`.
- **Cost ledger**: every LLM call records token usage + USD into `.devrel/state.db`'s `costs` table; `devrel cost [--month YYYY-MM]` aggregates.
- **`devrel doctor`**: project + env health checks with `--json` mode.
- **Console script entry-point**: `devrel = "devrel_swarm.cli:app"` in `pyproject.toml`.

### Changed

- **Repo restructure**: `agents/` → `src/devrel_swarm/core/`, `tools/` → `src/devrel_swarm/tools/`. `agents/config.py` renamed to `core/agent_config.py`.
- **Content agents** (Kai, Mox, Pax): replaced single `generate_with_revision` call with `quality.editorial.run_pipeline`. Falls back to legacy revision when no `.devrel/` project exists.
- **Dependencies**: added Typer, Rich, tomli-w. `pyproject.toml` deps now match `requirements.txt`.

### Deprecated / removed

- The SaaS / per-instance Fly + Next.js central-app direction was abandoned. Its branch is preserved as the `archive/v0-agentic-alpha` tag for reference; the local branch was removed.

### Known issues

- 22 pre-existing test drift cases on `main` (`test_sage::TestSageProductAreaDetection`, `test_search_tools::*`, `test_mcp_server::*`, plus a few in `test_llm`/`test_echo`/`test_kai`/`test_code_validator`/`test_instantly_client`). Not introduced by Phases 1-5; deliberately preserved at parity through the migration. Cleanup is its own follow-up.

### Deferred

- `devrel ask` — natural-language router (spec defers to v1.1).
- BudgetGate cap enforcement — costs are recorded; caps are not yet enforced.
- `devrel run --devrel | --sales | --marketing` sub-cycle flag variants.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG for v0.2.0"
```

---

## Task 5: Final verification

- [ ] **Step 1: Confirm `git status` is clean and the test suite still passes**

```bash
git status --short
.venv/bin/python -m pytest tests/ -q --no-header --tb=no --no-cov 2>&1 | tail -3
```
Expected: empty status, `707 passed, 22 failed`.

- [ ] **Step 2: Confirm worktrees + branches are clean**

```bash
git worktree list
git branch -a
git tag --list 'archive/*'
```
Expected: only `main` worktree (no `.worktrees/v0-agentic-alpha`); only the `main` branch (no `product/v0-agentic-alpha`); the `archive/v0-agentic-alpha` tag visible.

- [ ] **Step 3: Smoke-test the README's Quick Start example**

```bash
T=$(mktemp -d) && cd "$T"
ANTHROPIC_API_KEY=sk-ant-test devrel init --non-interactive --name probe --url https://probe.dev --github-repo probe/probe >/dev/null
ANTHROPIC_API_KEY=sk-ant-test devrel doctor 2>&1 | tail -3
echo "exit=$?"
cd - && rm -rf "$T"
```
Expected: doctor runs, exits 0 (with warnings on optional env vars, which is fine).

- [ ] **Step 4: Final commit log**

```bash
git log --oneline -15
```
Expected: a stack of focused commits including all Phases 1-5.

---

## Self-review checklist (already applied)

- **Spec coverage:** spec §"Migration path" steps mapped to:
  - Step 1 (tag-and-archive) → Task 1
  - Step 5 (Update README/CLAUDE.md) → Tasks 2 + 3
  - Steps 2-4 (salvage / delete / move) → already done in Phases 1-4
- **No placeholders:** every step has explicit commands or full markdown content.
- **Type / name consistency:** N/A (no code changes).
- **Reversibility:** all changes are reversible — the archive tag is a label, branch deletion is recoverable from the tag, README/CLAUDE.md/CHANGELOG are diff-revertable.

## Out of scope

- Fixing the 22 pre-existing failing tests — separate cleanup project. The CHANGELOG documents them as a known issue.
- BudgetGate cap enforcement — recorded but not enforced; left as a deferred follow-up.
- `devrel ask` natural-language router — spec defers to v1.1.
- Publishing to PyPI — `pipx install devrel-swarm` won't work end-to-end until the package is on PyPI; that's a release operation, not a phase task.
