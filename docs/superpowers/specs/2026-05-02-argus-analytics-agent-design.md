# Argus — Content Performance Analyst Agent — Design Spec

**Status:** Approved (2026-05-02)
**Author:** Daria Dovzhikova
**Scope:** New agent (13th in the devrel-origin pantheon)

## Why this exists

The current 12-agent system has three watchers — none of which measure post-publish content performance:

- **Watchdog** monitors infra/budget/output freshness.
- **Sentinel** audits content quality *before* publish.
- **Echo** listens for *external* brand mentions on social.

Nothing measures how content devrel-origin itself ships actually performs in the wild, nor does anything close the loop with optimization recommendations. Atlas can produce content week after week without any signal on which themes, headlines, or channels are working. Argus fills that gap.

## Role and scope

Argus is the post-publish content performance analyst. It pulls performance data from PostHog, GitHub, Instantly, and Echo's social-mentions table; ranks content deterministically; and uses an LLM to generate structured optimization recommendations. It **measures and recommends**; it does not **act**. Atlas/Iris/Mox/Nova retain control of execution.

This is a **B-mode agent** in the brainstorming taxonomy: reporter + recommender, no closed loop. A future v2 could promote it to closed-loop (feed winners into Iris's theme picker, kill underperforming Pax drips, request Nova A/B tests), but v1 deliberately stops at recommendation.

## Architecture

### Module layout

```
src/devrel_origin/core/argus.py         # agent class, scorer, LLM interpreter, schemas
src/devrel_origin/tools/analytics.py    # data collectors (one class per source)
prompts/argus_system.md                # system prompt (cached, Phase 8 pattern)
tests/test_argus.py                    # unit + integration tests
```

This mirrors the established agent-per-module convention (sentinel.py, nova.py, watchdog.py).

### Components

#### 1. Collectors (`tools/analytics.py`)

Each collector is a standalone class that returns `list[PerformanceMetric]`. One collector failure does not fail the whole report — the failing source is recorded in `PerformanceReport.sources_ok` and the report continues with degraded coverage.

| Collector | Source | Existing client | Primary metric |
|-----------|--------|-----------------|----------------|
| `PostHogCollector` | PostHog HTTP API | `tools/api_client.py:PostHogClient` | `page_views` (blog/landing) |
| `GitHubCollector` | GitHub API | `tools/github_tools.py` | `stars_delta` (repo) |
| `InstantlyCollector` | Instantly API | `tools/instantly_client.py` | `reply_rate` (email) |
| `SocialCollector` | `.devrel/state.db` `social_mentions` table | direct SQLite read (Echo writes) | `engagement_score` (social) |

Each collector exposes a single method:

```python
def collect(self, period: tuple[datetime, datetime]) -> list[PerformanceMetric]: ...
```

No collector talks to Argus directly; Argus orchestrates them.

#### 2. Scorer (`argus.py:_score_metrics`)

Pure Python, no LLM. Responsibilities:

- Group metrics by `content_type`.
- Per-type ranking by `primary_metric` descending.
- Compute `percentile` against the corpus baseline (the historical pool of all metrics of the same type, read from the `analytics_reports` table).
- Compute `wow_delta` when ≥2 weeks of history exist for that `content_id`.
- Tag obvious anomalies (z-score > 2.5 in either direction) so the LLM can flag them.

Output: ranked `list[PerformanceMetric]` with `percentile` and `wow_delta` fields populated.

#### 3. LLM interpreter (`argus.py:_generate_recommendations`)

- Model: Sonnet (matches Iris, Sage, Mox).
- System prompt cached at construction (Phase 8 pattern, applied to Kai/Mox/Pax/Rex).
- Input context: the ranked leaderboard (capped at top 10 + bottom 5 per content type, ~50 items max), trend signals computed by the scorer, and project-level voice/style summary (so recommendations stay on-brand).
- Output: structured `list[Recommendation]` via JSON-mode response, parsed into dataclasses.
- One LLM call per report.
- Cost: ~3-5k input tokens + ~1k output tokens ≈ **$0.03 per report** at current Sonnet pricing.

If the LLM call fails or returns unparseable JSON, the report degrades to scoreboard-only (no recommendations) and notes the failure in the markdown header. This matches Sentinel's existing "JSON-vs-API error split" pattern (Phase 7).

#### 4. Reporter (`argus.py:to_markdown` / `to_json`)

- `to_markdown()` produces a human-readable report:
  - Header with period, sources_ok, total content audited
  - Top 5 per content type
  - Bottom 3 per content type
  - Trend signals (3-7 bullets)
  - Recommendations grouped by `action` (double_down / retire / rewrite / retest / amplify / investigate)
- `to_json()` produces the raw `PerformanceReport` dataclass dump (for `analytics_reports` table and JSON output mode)
- Markdown report written to `.devrel/deliverables/analytics-YYYY-MM-DD.md`
- JSON report serialized into `analytics_reports` SQLite table for historical comparison

### Schemas

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ContentType = Literal["blog", "landing", "social", "email", "repo", "video"]
RecAction = Literal["double_down", "retire", "rewrite", "retest", "amplify", "investigate"]
TargetType = Literal["content", "theme", "channel"]

@dataclass
class PerformanceMetric:
    content_id: str               # "blog/2026-04-29-cli-launch"
    content_type: ContentType
    title: str
    url: str | None
    published_at: datetime
    primary_metric: float
    metric_name: str              # "page_views", "stars", "reply_rate", ...
    secondary_metrics: dict[str, float] = field(default_factory=dict)
    percentile: float | None = None    # 0-100, vs corpus baseline
    wow_delta: float | None = None     # % change vs prior week
    anomaly_flag: bool = False         # |z-score| > 2.5

@dataclass
class Recommendation:
    action: RecAction
    target: str                   # content_id or theme name
    target_type: TargetType
    rationale: str                # 1-3 sentences, plain English
    evidence: list[str]           # specific metric refs ("blog/X had 12% conversion vs 5% baseline")
    confidence: float             # 0.0-1.0

@dataclass
class PerformanceReport:
    period_start: datetime
    period_end: datetime
    top_performers: list[PerformanceMetric]      # top 5 per content_type
    bottom_performers: list[PerformanceMetric]   # bottom 3 per content_type
    trend_signals: list[str]                     # "Python topic +30% WoW"
    recommendations: list[Recommendation]
    sources_ok: dict[str, bool]                  # which collectors succeeded
    insufficient_data: bool = False              # set if <7 days of history
    llm_error: str | None = None                 # set if LLM call failed
```

### Recommendation action vocabulary

The closed set of actions (`double_down`, `retire`, `rewrite`, `retest`, `amplify`, `investigate`) is deliberate — it keeps recommendations actionable and makes a future closed-loop integration tractable (each action maps to a downstream agent). Semantics:

| Action | Meaning | Future consumer |
|--------|---------|-----------------|
| `double_down` | Theme/channel is winning; produce more like this | Iris (theme picker) |
| `retire` | Content/theme is consistently underperforming; stop investing | Iris, Mox |
| `rewrite` | Specific piece has potential but is poorly executed; redo it | Mox, Kai |
| `retest` | Inconclusive result; re-run with more samples or different cohort | Nova (experiments) |
| `amplify` | Already-good content is under-distributed; push harder on existing channels | Pax, Mox social |
| `investigate` | Anomaly the LLM can't explain; flag for human review | (human) |

## Data flow

```
.devrel/state.db ──── SocialCollector (Echo's social_mentions table)
PostHog API ────────── PostHogCollector
GitHub API ─────────── GitHubCollector
Instantly API ──────── InstantlyCollector
                                        │
                                        ▼
                              raw list[PerformanceMetric]
                                        │
                                        ▼
                              _score_metrics() (Python)
                                        │
                                        ▼
                              ranked + percentile-tagged metrics
                                        │
                                        ▼
                       _generate_recommendations() (Sonnet, 1 call)
                                        │
                                        ▼
                              PerformanceReport
                                        │
                ┌───────────────────────┼───────────────────────┐
                ▼                       ▼                       ▼
   .devrel/deliverables/     .devrel/state.db              stdout
   analytics-YYYY-MM-DD.md   analytics_reports table       (CLI)
```

## CLI surface

New top-level subgroup `analytics` with one verb (`report`). Argus is the first agent under it; future verbs (e.g., `analytics compare`, `analytics export`) can land in the same subgroup without disturbing other surfaces.

```
devrel analytics report [--since 7d] [--format md|json] [--push]
```

Flags:

- `--since` accepts a duration string (`7d`, `30d`, `90d`) or an ISO date. Default: `7d`.
- `--format` selects stdout output. Default: `md`. Both are always written to disk regardless.
- `--push` writes to the configured Slack/email channel (reuses Watchdog's notification path in `tools/notifications.py`).

The verb shape mirrors `devrel cost [--month YYYY-MM]` — on-demand reporter with optional push.

Schedulability: the existing scheduler (`devrel schedule install`) wraps any verb without modification, so weekly cadence is `devrel schedule install "analytics report --push" --weekly`. No scheduler changes needed.

## Atlas integration (`devrel run`)

A new stage 7 is appended to the Atlas pipeline, after Sentinel (stage 6):

```python
# atlas.py, simplified
if config.orchestration.analytics_in_run:
    argus_report = await Argus(...).run(period="last_7d")
    cycle_deliverables.append(argus_report)
```

Gated by a new config flag in `.devrel/config.toml`:

```toml
[orchestration]
analytics_in_run = true   # default true
```

Users who don't want it set `false`. Cost impact when enabled: ~$0.03/cycle (negligible against existing Atlas cycle costs).

Atlas writes its per-stage checkpoint (Phase 7 pattern: per-agent checkpoint flags) so a failed Argus call doesn't tank the whole cycle — Atlas marks stage 7 failed and continues.

## Storage

### `analytics_reports` table (new, in `.devrel/state.db`)

```sql
CREATE TABLE analytics_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    report_json TEXT NOT NULL,           -- full PerformanceReport serialized
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_analytics_reports_period ON analytics_reports(period_end);
```

Used for:

- Historical baseline computation (percentile rank against past corpus).
- WoW delta computation.
- Trend signal computation across runs.

Schema migration applied via the existing migration mechanism (whatever pattern Phase 1 established — Argus implementation will follow it, not invent a new one).

### Markdown deliverable

`.devrel/deliverables/analytics-YYYY-MM-DD.md`. Same convention as other deliverables.

## Error handling

| Failure | Behavior |
|---------|----------|
| One collector raises | `sources_ok[name] = False`, error logged, report continues with partial data |
| All collectors return empty | `insufficient_data = True`, report explains why, no LLM call made |
| LLM call fails / returns unparseable JSON | `llm_error = <reason>`, `recommendations = []`, scoreboard-only report shipped, header notes degradation |
| `<7` days of history available | `insufficient_data = True`, report ships anyway with a "preliminary — need more history" caveat in the header |
| SQLite write fails | Fail loudly (this is a hard error; we'd rather no report than a silently lost one) |

This matches the degradation patterns already established by Sentinel (Phase 7 JSON-vs-API split) and Watchdog (Phase 7 budget alert as % of cap).

## Testing

Pattern follows `tests/test_sentinel.py` and `tests/test_nova.py`:

| Test layer | Coverage |
|------------|----------|
| Unit: scorer | Synthetic `PerformanceMetric` lists, deterministic ranking, percentile/WoW math, anomaly flagging |
| Unit: each collector | Mocked HTTP/SQLite responses, error paths, empty returns |
| Unit: schema serialization | `to_json` / `from_json` round-trips for `PerformanceReport` |
| Integration: full pipeline | All collectors mocked, `LLMClient` mocked with canned JSON response, verify `Recommendation` dataclass parse + `to_markdown()` snapshot |
| Integration: Atlas stage 7 | Mocked Argus, verify Atlas calls it iff `analytics_in_run = true`, verify failure is non-fatal |

Targeting ≥90% line coverage on `argus.py` and the new collectors (matches existing agent test density).

## Cost

| Path | Cost per call |
|------|--------------|
| One report (Sonnet, ~4k in / ~1k out, system prompt cached) | ~$0.03 |
| Weekly schedule (52 reports/year) | ~$1.56/year |
| Atlas stage 7 (one per Atlas cycle, weekly) | ~$1.56/year additional |

Logged via existing cost-sink to `.devrel/state.db` `costs` table; surfaced by `devrel cost`.

## Out of scope (v1)

- **Closed-loop optimization.** Argus emits recommendations; agents do not yet consume them programmatically. The `Recommendation` schema is designed to support this in v2.
- **Per-channel custom KPIs.** v1 uses one primary metric per content type. Custom KPIs (e.g., "newsletter has its own engagement formula") deferred until users ask.
- **Real-time monitoring.** Argus runs on demand or on a schedule; it is not a streaming alert system. Watchdog handles operational alerts.
- **Cross-project benchmarks.** Argus compares a project's content against its own historical baseline, not against other projects'. Cross-project requires a hosted backend, which the CLI design rules out.

## Open questions

None remaining at spec-approval time. Implementation decisions deferred to the plan: exact migration mechanism, exact JSON-mode prompt format, exact Slack message template.

## References

- `docs/superpowers/specs/2026-04-29-devrel-origin-cli-design.md` — parent CLI spec
- `src/devrel_origin/core/sentinel.py` — pattern for audit-style agent
- `src/devrel_origin/core/nova.py` — pattern for analyst-style agent with structured output
- `src/devrel_origin/core/watchdog.py` — pattern for monitoring agent + notifications
- Memory: `project_devrel_origin.md` — Phase 8 system-prompt caching pattern, Phase 7 per-agent checkpoint flags, Phase 7 JSON-vs-API error split
