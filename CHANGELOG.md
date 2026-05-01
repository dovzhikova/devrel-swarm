# Changelog

## 0.2.1 — 2026-05-01

Wave 1 bug sweep — 13 high-impact fixes from the 2026-04-29 agent review. Every fix targets a bug that ships to users today: silent broken features, race conditions under concurrent stages, dead alerting paths, and output collisions on parallel runs.

### Fixed

- **Atlas**: race-safe per-agent cost attribution under `asyncio.gather` via `agent_context()` ContextVar (was: shared mutable `_current_agent` clobbered by concurrent stages).
- **Watchdog**: integration alert now fires for any unhealthy status (was: dead-code condition checking for a status the agent never emits). Firecrawl probe now uses a GET-able endpoint (was: POST-only `/v1/scrape` always returned 405).
- **Sentinel**: `_collect_content` now reads each agent's primary content key (was: only `"content"`, missing 6 of 9 agents per cycle).
- **Sage**: `champion_signal` is now actually set (was: declared on `TriagedIssue` but never assigned True). `CHURNING` sentiment gets an empathetic response branch (was: generic "added to triage queue").
- **Echo**: `posted_at` parsed from search results instead of `datetime.now()`. `is_question` uses a dedicated `QUESTION_SIGNALS` constant (was: magic slice of `ENGAGEMENT_SIGNALS[:8]`). Fixed `OpenClaw'` typo.
- **Iris**: unmatched themes route to a new `"other"` journey stage (was: defaulted to `"onboarding"`, systematically inflating onboarding friction). Early-return paths now log distinguishably.
- **Nova**: experiment IDs use `hashlib.sha256` for stability across process restarts (was: Python's randomized `hash()`). `DAILY_SIGNUPS_ESTIMATE` clamped to a floor of 10.
- **Kai**: `content_type` parameter now flows from caller (was: hardcoded `"tutorial"` for all calls including changelogs). Pipeline issue filter handles `list[str]` correctly (was: `isinstance(i, dict)` silently dropped every editorial flag).
- **Mox**: `email_campaign` failure now falls through to the editorial pipeline with a clean prose prompt (was: JSON-format-contaminated prompt corrupted by editorial stages). `PIPELINE_CONTENT_TYPE_MAP` covers all 6 routed content types.
- **Pax**: shared `_extract_icp_criteria` helper deduplicates ICP-extraction prompt + normalization across `_execute_prospect` and `_execute_prospect_personalize` (was: two divergent copies with different exception handling).
- **Vox**: output filename slugged + timestamped (was: hardcoded `tutorial.mp4` collided on parallel runs). FFmpeg subprocess calls in `assembler.py` and `overlay_renderer.py` have a 300s timeout + kill (was: no timeout — hung FFmpeg could block the pipeline indefinitely).
- **Dex**: `ast.AnnAssign` constants now appear in the symbol table (was: only `ast.Assign` visited; modern annotated constants invisible).
- **Rex**: parallel web search bounded by `Semaphore(3)` (was: 10+ unbounded simultaneous requests reliably 429'd by Brave/Firecrawl free tiers). Apollo domain guess preserves existing TLDs (was: `pendo.io` → `pendo.io.com`).

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
