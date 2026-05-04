# Changelog

## 0.2.4 — pre-publish polish (2026-05-04)

Final pre-publish pass across linting, packaging, and dependency footprint
landed the day of the PyPI tag. No agent or pipeline behavior changes for
pipx end users; the headline change is a lighter default install.

### Changed

- **Dependency footprint reduced**: `openai`, `playwright`, `pyautogui` moved from core dependencies into a new `[video]` optional extra. Default `pip install devrel-swarm` now skips ~150MB of Playwright browsers + pyobjc + the OpenAI SDK. Vox users opt in with `pip install 'devrel-swarm[video]'` (or `pipx install 'devrel-swarm[video]'`). Calling `TTSEngine` without the extra raises a clear `ImportError` pointing at the install command.
- **`tts_engine`**: `openai.AsyncOpenAI` is now imported lazily inside `_require_openai()`. Module-load no longer touches `openai`. Locked in by `tests/core/test_video_lazy_imports.py`.
- **Dropped unused dependencies**: `requests`, `aiohttp`, and `ffmpeg-python` had zero imports across `src/` and `tests/` and have been removed from `pyproject.toml`. Pure cruft from earlier scaffolding; CLAUDE.md already mandates `httpx` for all HTTP work.
- **Codebase ruff-clean**: full lint pass + format pass; CI now enforces both `ruff check` and `ruff format --check` (the format gate had been deferred since the original ruff adoption).

### Fixed

- **`load_agent_prompt` actually loads on-disk prompts now**: `_OPTIMIZE_DIR` had been resolving to `src/devrel_swarm/optimize/` (a path that never existed) since the Phase 1 src/-layout move, so every agent silently fell through to its inline default. Replaced with a `_resolve_optimize_dir()` walk-up that finds the repo root via `pyproject.toml`+`optimize/` co-location, returns `None` for installed users (preserving their current behavior), and accepts both layouts the repo currently uses (top-level `optimize/{agent}/` and nested `optimize/agents/{agent}/`). Dev-tree users will see the maintainer's optimized prompts taking effect for the first time. Coverage in `tests/core/test_load_agent_prompt.py`.
- **`tests/test_vox.py`**: `DesktopRecorder` tests now skip on headless Linux (CI was breaking because `pyautogui` needs an X11 `DISPLAY`). Marked with `_NEEDS_DISPLAY`.

### Internal

- 47-file ruff lint pass: `zip(..., strict=True)` everywhere, import sorting, unused-import removal, `list()` over copy-comprehensions.
- 92-file ruff format pass (whitespace-only).
- `pyproject.toml`: added `extend-exclude` for `examples/`/`optimize/`/`landing/` (script-style, not library code), per-file ignores for tests, and ignores for `C901`+`B008`.
- CLAUDE.md install instruction updated from the dead `pip install -r requirements.txt` to `pip install -e ".[dev]"` + the pipx end-user route. `output/` (Vox's default render dir) added to `.gitignore`.
- New regression coverage: 7 tests for `load_agent_prompt` + 8 tests for video lazy-import. Suite is now 815 pass / 21 xfail / 76% coverage.

## 0.2.4 — 2026-05-03

Argus — the 13th agent, plus a 20-item enhancement pass derived from a multi-lens code review of the v1 ship.

### Added

- **Argus**: post-publish content performance analyst. Pulls metrics from PostHog, GitHub, Instantly, and Echo's `social_mentions` table; ranks deterministically; emits structured `Recommendation` objects via a single Sonnet call with a closed action vocab (`double_down`, `retire`, `rewrite`, `retest`, `amplify`, `investigate`). Sits beside Watchdog (infra) and Sentinel (pre-publish quality) as the post-publish watcher.
- **`devrel analytics report`**: produce a performance report for the last `--since` window. `--push` sends to configured Telegram + email; `--push-on-partial` overrides the all-sources-green gate.
- **`devrel analytics history CONTENT_ID`**: metric trajectory of one piece of content across all reports. Markdown table or `--format json`.
- **`devrel analytics diff PERIOD_A PERIOD_B`**: side-by-side comparison of two reports, sorted by absolute %-delta. Surfaces top movers, plus `new` and `gone` classifications.
- **`devrel analytics calibration`**: scores past `double_down`/`retire` recommendations against subsequent metric history. Reports per-action hit rate, avg confidence, lift vs coin-flip, and high/low confidence calibration.
- **`devrel analytics summary`**: cross-project rollup. Walks every `.devrel/state.db` under `--root` (default `$HOME`) and aggregates total recommendations, metric history rows, last report period, and Argus spend per project.
- **Schema v3**: `metric_history(content_id, period_end, primary_metric, metric_name, content_type)` with composite PK and a `(content_id, period_end DESC)` index. Indexed time-series for week-over-week baselines; replaces O(N) JSON-blob deserialization.
- **Schema v4**: `analytics_recommendations(report_id, action, target, source_ids_json, confidence, first_seen_period, applied_at, ...)`. Per-rec rows queryable by action/target without parsing report blobs. The v2 closed-loop routing bus.
- **Recommendation lifecycle**: when `(action, target)` re-emerges in a later report, `first_seen_period` carries over from the earliest match. The markdown report tags recommendations stale ≥2 weeks as `[STALE Nw]`.
- **Content brief generation**: for each `double_down`/`amplify`/`rewrite` recommendation, Argus stages a Mox-ready brief at `.devrel/deliverables/argus-brief-<period>-<action>-<target>.md` with rationale, evidence, source IDs, and a tailored next-step shell command.
- **Optional Atlas Stage 5b**: `[orchestration].analytics_in_run = true|false` (default `true`) gates Argus inside `devrel run`. Failures surface as `argus_report = {"error": "<reason>"}` rather than aborting the cycle.
- **Structured logs**: five `logger.info` events at `gather_complete`, `baselines_loaded`, `scored`, `recommendations_generated`, `persisted`. An operator can reconstruct any run from log events alone.
- **Documentation**: new `docs/` tree — quickstart, Argus agent reference, analytics CLI reference, cookbook.

### Changed

- **InstantlyCollector**: now filters campaigns by `created_at` (or `updated_at`) within the requested period. Without this filter, `--since 7d` and `--since 90d` returned identical email metrics.
- **CLI cost-sink**: `devrel analytics report` standalone runs now register the cost-sink so `devrel cost` accurately reflects Argus spend.
- **Argus system prompt**: cached at `__init__` instead of re-read on every LLM call (matches the Phase 8 Kai/Mox/Pax/Rex pattern).
- **Argus SQLite I/O**: `_persist`, `_load_baselines`, and `SocialCollector.collect` now wrap blocking calls with `asyncio.to_thread`. Eliminates event-loop stalls during the weekly cycle.
- **`Recommendation.source_ids`**: new `list[str]` field (default `[]`) carrying the `content_id` values backing each recommendation.
- **Echo schema contract**: `SocialCollector` now `PRAGMA table_info`-checks `social_mentions` on first read. Schema drift now warns instead of silently producing partial data.
- **LLM prompt truncation**: when the 50-line cap fires, partial sections append `...(N more X items omitted)` and fully-dropped content types are listed under `### TRUNCATED`.
- **`AgentConfig.analytics_in_run`**: promoted from `getattr` fallback to a typed dataclass field with TOML-key documentation in `config/agent_config.yaml`.

### Fixed

- `--push` flow now constructs `NotificationService` via `NotificationConfig` from env vars (the previous `from_env()` and `send_digest(subject=, body=)` calls would have raised `AttributeError` and `TypeError` at runtime).
- `--push` is now gated on `sources_ok` all-green by default, with `--push-on-partial` to override.
- Atlas Stage 5b honors the `resume_stage` guard and writes a checkpoint, so a crash between Stage 5b and Stage 6 doesn't re-run Sentinel on resume.

### Performance

- **Baselines lookup**: O(N) JSON blob deserialization replaced with a single indexed `SELECT` against `metric_history`. At 2k content IDs the prior approach allocated ~500 KB per call; the new path scales linearly with N.

### Tests

- 800 pass / 21 baseline fail — exact parity with the documented Phase 7-8 baseline (no new failures introduced; +33 tests for Argus + collectors + CLI + Atlas in v1, +23 more in v2).

## 0.2.3 — 2026-05-01

Wave 3 polish — final batch from the 2026-04-29 agent code review. No behavior changes; pure cleanup of papercuts the next round of feature work would otherwise re-discover.

### Changed

- **Atlas**: `process_draft` now uses `subprocess.run([editor, path])` with `shutil.which` validation — no shell-injection surface, "editor not found" surfaces as a log line rather than a silent no-op. Self-improvement step splits `ImportError` (module not installed) from generic `Exception` (module crashed) for diagnosable logs.
- **Echo**: `search_limit` exposed as constructor parameter (default 20) — projects with high mention volume can scan more deeply, projects with low volume can be cheaper.
- **Iris**: theme-extraction prompt no longer hardcodes a single source value. The LLM now infers sources from the signal list, so multi-source feedback (Discourse, support tickets, etc.) is correctly labeled.
- **Sage**: classification keyword vocabularies extracted to module-level constants (`CHURN_SIGNALS`, `FRUSTRATION_SIGNALS`, `BUG_KEYWORDS`, `CRITICAL_KEYWORDS`). Single source of truth — no more silent divergence between sentiment, category, and priority logic.
- **Nova**: `from scipy import stats` moved to module-level (was deferred inside `calculate_sample_size`). MDE-severity logic now has an explanatory comment.
- **Vox**: desktop recorder FFmpeg stderr piped to `PIPE` and logged on non-zero exit (was: discarded silently, failures showed up as cryptic downstream errors). TTS `stream_to_file()` runs in `loop.run_in_executor` so the event loop isn't blocked during audio streaming.

### Performance

- **Kai/Mox/Pax/Rex**: `SYSTEM_PROMPT` cached at construction (was: file re-read on every `execute()` call). Removes a per-call stat+read from the hot path; meaningful for bulk operations like Pax's per-contact personalization.

## 0.2.2 — 2026-05-01

Wave 2 correctness gaps from the 2026-04-29 agent review. Each fix targets a silent diagnosability gap — wrong-but-plausible output that looked like success.

### Fixed

- **Atlas**: per-agent checkpoint flags + Stage 6 checkpoint. Resume from a partial-stage failure now re-runs only the failed agent (was: re-ran all agents in the stage). Instantly sync now checkpoints — a network failure after the brand audit no longer drops Mox/Pax campaign data.
- **Watchdog**: real `output_age_hours` parsed from agent timestamps (was: always 0 or 999). Budget alert as % of cap (was: hardcoded 500k tokens with no link to budget).
- **Sentinel**: split JSON-vs-API error logging — operators can now tell a malformed LLM response from a rate-limit error. Structural-audit scoring normalized to 1-100 scale (was: capped at ~70, incomparable with LLM path).
- **Iris**: `SIMILARITY_THRESHOLD` promoted to module constant with calibration comment. `_find_content_opportunities` now produces actionable briefs incorporating the theme's top recommended action (was: title echoes).
- **Nova**: funnel result includes `data_source` field marking values as `"default_estimates"` vs `"api"` (was: hardcoded mock counts presented as real).
- **Kai**: exception path now sets `status="error"` + `content=""` (was: silent empty-content with `status="generated"`). Uses `logger.exception` for tracebacks.
- **Mox**: `revision` schema unified with Kai's (`remaining_issues` key) — consistent contract across content agents.
- **Pax**: `_load_prompt` migrated to shared `base.load_agent_prompt` (was: hardcoded source-relative path that broke outside the source tree). `_execute_campaign` now None-guards `llm_client` with a clear `"skipped"` status return.
- **Dex**: class-body traversal switched to `ast.walk` so nested classes / decorated methods / staticmethods appear in symbol output. `repo_path` now defaults from `.devrel/` project root (was: process-cwd-dependent).
- **Rex**: JSON parse failure sets `status="parse_error"` + preserves `raw_content` (was: stored raw string under `content`, breaking consumers expecting a dict).

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
