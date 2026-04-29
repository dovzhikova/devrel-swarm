# devrel-swarm CLI — Design Spec

**Status:** Approved (2026-04-29)
**Supersedes:** `docs/superpowers/specs/2026-04-17-devrel-swarm-product-design.md` (SaaS direction) and `docs/superpowers/plans/2026-04-18-devrel-swarm-v0-agentic-alpha.md` (per-instance Fly plan)

## Why this exists

The 2026-04-17 product spec aimed at a hosted SaaS with per-customer Fly instances orchestrated by a Next.js central app. Phase A of that plan (SQLite storage, BudgetGate, HTTP bridge) is partially built on `product/v0-agentic-alpha`. We are pivoting away from that direction. The new product is a developer-first CLI that operates on a project repo the way `git`, `npm`, and `cargo` do, and that produces marketing/content output of professional editorial quality — far above what a one-pass revision loop can deliver.

This spec replaces the SaaS direction. The 12-agent system (Atlas, Sage, Echo, Iris, Nova, Kai, Vox, Dex, Rex, Pax, Mox, Sentinel, Watchdog) is preserved unchanged in its agent-level behaviour; the CLI wraps it.

## Shape

A `pipx`-installable Python CLI named `devrel`. Operates on the current working directory. Each project gets a `.devrel/` directory containing config, voice/style/slop files, knowledge base, deliverables, and per-project state.

## On-disk layout

After `devrel init`:

```
my-project/
  .devrel/
    config.toml          # product_name, repo, schedule, model, budget caps
    voice.md             # tone profile + sample passages       (commit)
    style.md             # house style + per-content targets    (commit)
    slop-blocklist.md    # banned phrases                       (commit)
    kb/                  # markdown corpus, TF-IDF indexed      (gitignored)
    deliverables/        # generated outputs                    (gitignored)
    context/             # weekly archives + cross-run memory   (gitignored)
    state.db             # SQLite: jobs, costs, checkpoints     (gitignored)
    .env                 # local secrets                        (gitignored)
    .gitignore           # auto-managed by `devrel init`
```

Voice, style, slop, and config files are intended to be committed — they encode the project's editorial contract and should diff/review like any other source file. Everything else is gitignored.

Cross-project shared secrets (e.g., `ANTHROPIC_API_KEY`) live at `~/.devrel/secrets.env`, read with lower precedence than `.devrel/.env`.

## CLI surface

Single binary `devrel` with verb/role-grouped subcommands. Agent names (Kai/Pax/etc.) are an implementation detail and never appear in the public CLI surface.

```
# bootstrap & health
devrel init                          # bootstrap .devrel/ in cwd
devrel doctor                        # check env, API keys, KB freshness
devrel cost [--month YYYY-MM]        # token + $ report

# pipelines
devrel run                           # full weekly (DevRel + Sales + Marketing)
devrel run --devrel                  # subset
devrel run --sales
devrel run --marketing
devrel run --health

# DevRel
devrel triage [--days N]             # GitHub issue triage (Sage)
devrel listen [--platforms ...]      # Reddit/HN/X (Echo)
devrel synthesize                    # theme extraction (Iris)
devrel experiment <hypothesis>       # A/B + power analysis (Nova)

# Content
devrel content draft <prompt>        # quality-pipelined content (Kai)
devrel content audit <file>          # run quality pipeline on existing draft
devrel content slop <file>           # just the anti-slop pass
devrel docs build                    # AST-based docs (Dex)
devrel video record <script>         # Vox

# Sales
devrel intel <competitor>            # Rex competitor intel
devrel sales outreach <company>      # Pax cold email
devrel sales battlecard <competitor>
devrel sales sequence <campaign>

# Marketing
devrel marketing blog <topic>
devrel marketing landing <topic>
devrel marketing social <topic>
devrel marketing campaign <brief>

# Knowledge base
devrel kb add <url>
devrel kb list
devrel kb refresh

# Config & schedule
devrel config get <key>
devrel config set <key> <value>
devrel schedule install              # GH Actions workflow + cron template
devrel schedule list
devrel schedule remove

# Convenience
devrel ask "<natural language>"      # router on top of verb commands
devrel deliverables list
devrel deliverables show <id>
```

Global flags: `--json`, `--quiet`, `--model {sonnet|opus|haiku}`, `--dry-run`.

## Quality pipeline

Every content-producing run (Kai, Mox, Pax, Vox script) flows through a fixed 8-stage pipeline. This is the lever that turns "AI-written content" into something that passes a senior editor's bar.

```
1. Generate          KB-grounded; voice.md + style.md + previous_titles in prompt
2. Developmental     critique+revise loop, scoring structure, argument, hook
3. Line edit         critique+revise loop, scoring rhythm + voice fidelity
4. Copy edit         critique+revise loop, scoring grammar, code blocks, consistency
5. Anti-slop         regex blocklist + LLM lint vs. slop-blocklist.md;
                       on fail, runs one targeted rewrite against flagged spans;
                       if the rewrite still fails, abort loud with a report
                       listing the offending phrases and their locations
6. Reader persona    "skeptical senior backend dev" persona scores 1-10,
                       flags weak sections with quoted excerpts
7. Readability       Flesch-Kincaid, sentence-length variance, jargon density
                       checked against per-content-type targets in style.md
8. Brand audit       Sentinel (existing 6-dim audit, unchanged)

→ deliverables/ + revision-trace.json (rounds, scores, all critique outputs)
```

Stages 2–4 each have their own system prompt and rubric and run as discrete `generate_with_revision` loops with `min_score=7`, `max_rounds=2`. Stage 5 is self-correcting: it runs its own targeted rewrite once, and only if that rewrite still trips the blocklist does it abort. Stages 6 and 7 are scoring-only on the stage-5 output; if either fails its threshold, control returns to stage 4 (copy edit) for one final revision pass with the failed rubric attached, and the pipeline then re-runs stages 5–7 once. A second failure on 6 or 7 logs the score and continues — output is still written, but flagged in `revision-trace.json`. Sentinel runs unchanged at the very end and writes its audit alongside the piece.

**Cost trade-off (explicit):** roughly 3-4× the LLM spend per piece versus the current single revision loop. With Anthropic prompt caching, ~2.5×. Stages 5 (slop lint), 6 (persona), and 7 (readability scoring) use Haiku to absorb most of the volume. BudgetGate stays in to enforce caps; the existing `tools/storage.py` + `BudgetGate` from Phase A are reused as-is.

## Code structure

```
devrel-swarm/
  src/devrel_swarm/
    __init__.py
    cli/                  Typer subcommands, one file per top-level verb
      __init__.py         Typer app + version
      init.py             devrel init
      doctor.py           devrel doctor
      cost.py             devrel cost
      run.py              devrel run [--devrel|--sales|--marketing|--health]
      triage.py           devrel triage
      listen.py           devrel listen
      synthesize.py       devrel synthesize
      experiment.py       devrel experiment
      content.py          devrel content draft|audit|slop
      docs.py             devrel docs build
      video.py            devrel video record
      intel.py            devrel intel
      sales.py            devrel sales outreach|battlecard|sequence
      marketing.py        devrel marketing blog|landing|social|campaign
      kb.py               devrel kb add|list|refresh
      config.py           devrel config get|set
      schedule.py         devrel schedule install|list|remove
      deliverables.py     devrel deliverables list|show
      ask.py              devrel ask (natural-language router)
    core/                 existing agents/ moved here, unchanged surface
      atlas.py
      sage.py … (all 12 agents)
      base.py
      llm.py
      config.py           (renamed → agent_config.py to disambiguate from cli/config.py)
      types.py
      video/
    tools/                existing tools/ moved here
    quality/              NEW: 5-lever quality pipeline
      voice.py            load + inject voice.md
      style.py            load + inject style.md + per-content-type targets
      slop.py             regex blocklist + LLM lint pass
      editorial.py        3-stage editorial loop (replaces single critique)
      persona.py          skeptical-dev reader test
      readability.py      Flesch-K + sentence variance + jargon density
    project/              NEW: project bootstrap & config
      init.py             writes .devrel/ scaffold (idempotent)
      config.py           TOML loader, env-var override, secret resolution
      paths.py            cwd-walk to find nearest .devrel/, like git
      state.py            wraps existing storage.InstanceStorage as project-state DB
  pyproject.toml          [project.scripts] devrel = "devrel_swarm.cli:app"
  README.md
  CHANGELOG.md
  tests/
    cli/                  Typer CliRunner per subcommand
    quality/              slop blocklist, readability, persona pass
    project/              init scaffolding, cwd-walk, config loading
    (existing tests preserved, paths updated)
```

Atlas keeps its existing public surface (`run_weekly_cycle`, `delegate`, etc.); the CLI wraps it instead of replacing it. Non-content agents (Sage, Echo, Iris, Nova, Dex, Rex, Watchdog, Sentinel) are unchanged. The four content-producing agents (Kai, Mox, Pax, and Vox's script generator) replace their current single `generate_with_revision` call with a call to `quality.editorial.run_pipeline(draft, content_type, project_paths)`, which orchestrates stages 2–8. The existing `agents/llm.py::generate_with_revision` stays as the building block used by each editorial stage internally.

## Tech choices

| Concern | Pick | Notes |
|---|---|---|
| CLI framework | Typer | Type-hint-first, generates rich `--help`, plays well with subcommands |
| Output formatting | Rich | Pretty terminal output; `--json` flag emits machine-readable |
| Config format | TOML | stdlib `tomllib` (3.11+); already used by `pyproject.toml` |
| Logging | structlog → stderr | JSON mode for CI |
| Default model | Claude Sonnet 4.6 | Haiku 4.5 for stages 5–7; Opus opt-in via `--model opus` |
| HTTP client | httpx (async) | Existing |
| Storage | SQLite via existing `InstanceStorage` | Rebadged as project state DB, lives at `.devrel/state.db` |
| Distribution | `pipx install devrel-swarm` | Python 3.12+ |
| Testing | pytest + pytest-asyncio + respx + Typer's CliRunner | |

## Migration from current state

1. Tag-and-archive the SaaS branch: `git tag archive/v0-agentic-alpha product/v0-agentic-alpha`, then leave the branch in place but stop building on it.
2. Mark the superseded plan with a deprecation header pointing to this spec.
3. Salvage from Phase A: `BudgetGate`, `InstanceStorage` (rebadged as `project/state.py` consumer), and the A.4 hardening test patterns. The A.4 hardening commit on the worktree branch can be cherry-picked or dropped — its security contributions (path traversal, fail-closed) are not needed in a CLI that runs locally with no inbound network surface.
4. Delete: `tools/http_bridge.py`, `tools/storage.py`'s job/cost endpoints if the FastAPI dep is dropped, `central-app/` references in docs.
5. Move `agents/` → `src/devrel_swarm/core/`, `tools/` → `src/devrel_swarm/tools/`. Imports updated globally.
6. New `pyproject.toml` package metadata with `[project.scripts] devrel = "devrel_swarm.cli:app"`.
7. Update `README.md`, `CLAUDE.md` to match CLI direction. Drop SaaS / Fly / Next.js sections.

## Out of scope

- Web dashboard / Next.js central app
- Multi-tenant SaaS / Fly per-instance provisioning
- HTTP bridge / FastAPI server
- Auth, billing, user accounts
- Single-binary distribution (deferred until/if traction warrants — pipx is the v1 distribution)

## Success criteria

- `pipx install devrel-swarm` works on Python 3.12+ (tested on macOS + Ubuntu)
- `devrel init` in a fresh repo scaffolds a complete `.devrel/` in <5s
- `devrel content draft "<prompt>"` produces output the slop filter cannot reject and Sentinel scores ≥7/10 on the first run, against an empty KB
- `devrel run` against the existing OpenClaw setup produces all current deliverable types (architecture-overview, community-triage, feedback-synthesis, growth-experiment, tutorial, plus campaign/outreach/battlecard/social where applicable) — at parity with `python -m agents.atlas --weekly-cycle`
- `quality/` package has ≥80% line test coverage
- `devrel content draft` walltime ≤90s with prompt caching
- All 12 existing agents still pass their existing tests after the move to `src/devrel_swarm/core/`

## Implementation phasing

This spec is large enough that a single execution plan would be unwieldy. The implementation plan that follows should phase the work, roughly:

1. **Repo restructure** — package move (`agents/` → `src/devrel_swarm/core/`, `tools/` → `src/devrel_swarm/tools/`), `pyproject.toml` rewrite, all existing tests still pass after the move.
2. **Project bootstrap** — `project/init.py`, `project/config.py`, `project/paths.py`, `project/state.py`; `devrel init` and `devrel doctor` commands; `.devrel/` scaffold tested against a fixture repo.
3. **Quality pipeline** — `quality/` package end-to-end (voice, style, slop, editorial, persona, readability); replace single critique call in Kai/Mox/Pax/Vox; revision-trace JSON output.
4. **CLI surface** — Typer subcommand modules for every verb in §3 above; Rich/JSON output; global flags.
5. **Migration & docs** — archive `product/v0-agentic-alpha`, rewrite README/CLAUDE.md, mark superseded plan with deprecation header, ship CHANGELOG.

Each phase is independently shippable and testable. Phase 1 is a no-behaviour-change restructure; Phase 2 unblocks the rest.

## Open questions (non-blocking, decided in implementation)

- Exact wording of the four "skeptical senior backend dev" persona rubric criteria — drafted in the persona module and tuned against the OpenClaw KB.
- Default per-content-type readability targets in `style.md` — initial values seeded from analysis of dariadovzhikova.com's current published posts; adjustable per project.
- Whether `devrel ask "..."` ships in v1 or v1.1 — leaning v1.1 to keep the v1 surface tighter, but the implementation cost is small (single Claude call to map intent → subcommand).
