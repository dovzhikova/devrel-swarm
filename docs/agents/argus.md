# Argus — content performance analyst

Argus is the 13th agent and the post-publish counterpart to Sentinel (which audits content *before* it ships) and Watchdog (which monitors infra and budget). Where Sentinel asks "is this on-brand", Argus asks "is what we already shipped actually working".

## What it does

1. Pulls metrics from four sources in parallel.
2. Ranks them deterministically per content type.
3. Sends the ranked leaderboard to Sonnet with a closed action vocabulary.
4. Writes a structured report and stages downstream content briefs for actionable recommendations.

It measures and recommends. It does not act. Atlas, Iris, Mox, and Nova retain control of execution. The schema is forward-compatible with a closed-loop v2.

## Data sources

| Collector | Source | Primary metric |
|---|---|---|
| `PostHogCollector` | PostHog event API | `page_views` per URL (blog/landing) |
| `GitHubCollector` | GitHub API | `stars_delta_7d` per repo |
| `InstantlyCollector` | Instantly API | `reply_rate` per email campaign |
| `SocialCollector` | Echo's `social_mentions` SQLite table | `engagement_score` per own-post mention |

Each collector isolates its own failures. If PostHog returns 503, Argus marks `sources_ok["posthog"] = False` and ships a degraded report from the remaining three sources rather than aborting.

## Closed action vocabulary

Argus's recommendations are typed. The LLM must pick exactly one of:

| Action | Meaning |
|---|---|
| `double_down` | Theme/channel is winning. Produce more of this kind of content. |
| `retire` | Content/theme is consistently underperforming. Stop investing. |
| `rewrite` | Specific piece has potential but is poorly executed. Redo it. |
| `retest` | Result is inconclusive. Re-run with more samples or a different cohort. |
| `amplify` | Already-good content is under-distributed. Push it harder on existing channels. |
| `investigate` | Anomaly the LLM cannot confidently explain. Flag for human review. |

Confidence below 0.5 always maps to `investigate` — the prompt enforces this, so the report never carries a directional recommendation backed by weak signal.

## Output

A `PerformanceReport` carrying:

- `period_start`, `period_end`
- `top_performers` and `bottom_performers` (5 top + 3 bottom per content type)
- `trend_signals` (3-7 short strings the LLM extracts)
- `recommendations` (`list[Recommendation]`)
- `sources_ok` (per-collector health dict)
- `insufficient_data`, `llm_error` (degradation flags)

Each `Recommendation` has:

```python
@dataclass
class Recommendation:
    action: Literal["double_down", "retire", "rewrite", "retest", "amplify", "investigate"]
    target: str                  # content_id, theme name, or channel
    target_type: Literal["content", "theme", "channel"]
    rationale: str               # 1-3 sentences of plain English
    evidence: list[str]          # specific metric refs
    confidence: float            # 0.0-1.0
    source_ids: list[str]        # content_ids backing the rec (1-5 entries)
    first_seen_period: str | None  # earliest period this rec was made (lifecycle)
```

## How it runs

Two entry points:

**Standalone** (ad-hoc, on demand):

```bash
devrel analytics report --since 7d
```

**Inside the weekly cycle** (Atlas Stage 5b, after Sentinel, before OKR compilation):

```toml
# .devrel/config.toml or config/agent_config.yaml
[orchestration]
analytics_in_run = true   # default; set to false to skip
```

When run inside the weekly cycle, `SharedContext.argus_report` carries `PerformanceReport.to_json()` for downstream consumers (and `{"error": "<reason>"}` if the stage fails — failures don't abort the cycle).

## Persistence

Three SQLite tables in `.devrel/state.db`:

- **`analytics_reports`** — JSON archive of each report, indexed by `period_end`
- **`metric_history`** — `(content_id, period_end, primary_metric, metric_name, content_type)` time-series. Composite PK; index on `(content_id, period_end DESC)`. Used for week-over-week baselines.
- **`analytics_recommendations`** — per-rec rows with `action`, `target`, `source_ids_json`, `confidence`, `first_seen_period`, `applied_at`. Queryable by action/target without parsing the report blob; this is the v2 closed-loop routing bus.

A markdown deliverable also lands at `.devrel/deliverables/analytics-YYYY-MM-DD.md`.

## Recommendation lifecycle

When the same `(action, target)` re-emerges in a later report, `first_seen_period` carries over from the earliest match. The markdown report tags any recommendation older than 2 weeks with `[STALE Nw]`. A `retire blog/x` recommendation that's still being made 4 weeks later is louder signal than the first time it appeared.

## Calibration

`devrel analytics calibration` scores how well past recommendations panned out:

- `double_down` "held" if the avg post-period metric for source content is ≥ 90% of the value at `first_seen_period`.
- `retire` "held" if the max post-period metric is ≤ 110% of the anchor (no recovery).
- Other actions are unscored — they need human input on what was acted on.

The output reports per-action hit rate, average confidence, lift vs coin-flip (rate − 0.5), and separate hit rates for high-confidence (≥0.8) and low-confidence (<0.5) recs. After a few weeks of history, this tells you whether to trust Argus's confidence scores or treat them as noise.

## Downstream artifacts

For each `double_down`, `amplify`, or `rewrite` recommendation, Argus stages a Mox/Kai-ready brief at `.devrel/deliverables/argus-brief-<period>-<action>-<target>.md`. The brief carries the rationale, evidence, source IDs, and a tailored next-step shell command:

- `double_down` → `devrel content draft '<target> — follow-up post' --type tutorial`
- `amplify` → `devrel marketing social '<target>' --channels reddit,hn,twitter`
- `rewrite` → `devrel content audit deliverables/<file>` then redraft

`retire`, `investigate`, and `retest` are not content tasks and are skipped — they need human or Nova decisions.

## Cost

One Sonnet call per report: ~3-5k input tokens + ~1k output tokens ≈ **$0.03 per report** at current pricing. Logged to `.devrel/state.db.costs` via the cost-sink, surfaced by `devrel cost`. A weekly schedule + Atlas Stage 5b is roughly $3.10/year — negligible.

## Failure modes

| What fails | What happens |
|---|---|
| One collector raises | `sources_ok[name] = False`, report continues with degraded coverage |
| All collectors return empty | `insufficient_data = True`, no LLM call, report explains why |
| LLM returns unparseable JSON | `llm_error = <reason>`, scoreboard-only report (no recs) |
| `<7` days of history | Report ships anyway with a "preliminary" caveat |
| Echo's table missing required column | `SocialCollector` logs a clear warning and returns `[]` (no silent partial data) |
| `--push` after partial-data run | Skipped by default; pass `--push-on-partial` to override |

## See also

- [`docs/cli/analytics.md`](../cli/analytics.md) — full CLI reference for the five `analytics` verbs
- [`docs/cookbook.md`](../cookbook.md) — recipes including the calibration loop and weekly cron
- [`docs/superpowers/specs/2026-05-02-argus-analytics-agent-design.md`](../superpowers/specs/2026-05-02-argus-analytics-agent-design.md) — original design spec
