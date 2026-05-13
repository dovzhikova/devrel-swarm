# `devrel analytics` — CLI reference

The `analytics` subgroup wraps Argus, the post-publish content performance analyst. Five verbs, all of which can run from inside any `.devrel/`-bootstrapped project.

The agent itself is documented at [`docs/agents/argus.md`](../agents/argus.md). This file is the operator's reference.

## Verbs at a glance

| Verb | What it does | Hits the LLM? | Hits external APIs? |
|---|---|---|---|
| `report` | Pull metrics, score, recommend, persist | Yes (1 call) | Yes (4 collectors) |
| `history` | Metric trajectory of one piece across reports | No | No |
| `diff` | Top movers between two periods | No | No |
| `calibration` | Did past recommendations pan out? | No | No |
| `summary` | Cross-project rollup | No | No |

Only `report` makes paid LLM calls. Every other verb reads `.devrel/state.db` and shells out nothing.

---

## `devrel analytics report`

Produce a performance report for the last `--since` window.

### Synopsis

```bash
devrel analytics report \
  [--since 7d] \
  [--format md|json] \
  [--push] \
  [--push-on-partial]
```

### Options

- `--since DURATION` — lookback window. Accepts `Nd` (days), `Nw` (weeks), `Nm` (~30 days), `Ny` (~365 days). Default: `7d`.
- `--format md|json` — stdout format. Default: `md`. Both modes always write the markdown deliverable to `.devrel/deliverables/analytics-YYYY-MM-DD.md`.
- `--push` — push the markdown report to configured Telegram + email channels (`TELEGRAM_BOT_TOKEN`, `EMAIL_SENDER`, etc.). Skipped if any source failed; override with `--push-on-partial`.
- `--push-on-partial` — bypass the all-sources-green push gate. Use when you knowingly want a partial digest sent.

### What it does

1. Runs four collectors in parallel: PostHog, GitHub, Instantly, Echo's `social_mentions` table.
2. Scores metrics deterministically per content type (percentile, week-over-week delta, anomaly flag).
3. Sends a bounded leaderboard (top 10 + bottom 5 per content type, capped at 50 lines) to Sonnet.
4. Parses structured `Recommendation` objects from the response.
5. Writes:
   - `.devrel/deliverables/analytics-YYYY-MM-DD.md` (human-readable)
   - One row in `analytics_reports` (JSON archive)
   - One row per metric in `metric_history` (indexed time-series)
   - One row per recommendation in `analytics_recommendations` (v2 routing bus)
   - One Mox-ready brief per `double_down`/`amplify`/`rewrite` rec at `.devrel/deliverables/argus-brief-*.md`

### Cost

~$0.03 per call (Sonnet, ~3-5k in / ~1k out, system prompt cached at construction). Logged to `.devrel/state.db.costs`.

### Examples

```bash
# Default 7-day report
devrel analytics report

# 30-day window, JSON to stdout
devrel analytics report --since 30d --format json

# Weekly digest, pushed to Telegram + email
devrel analytics report --since 7d --push

# Push even if PostHog was down (you'll get a partial report)
devrel analytics report --push --push-on-partial
```

---

## `devrel analytics history`

Show the metric trajectory of one piece of content across all persisted reports.

### Synopsis

```bash
devrel analytics history CONTENT_ID [--format md|json]
```

### What it does

Reads `metric_history` directly. Returns rows ordered by `period_end ASC` with the period-over-period percentage delta computed in-line.

### Examples

```bash
$ devrel analytics history blog/cli-launch
# History for `blog/cli-launch` (blog)

| period_end | page_views | delta |
|---|---|---|
| 2026-04-18 | 100 | — |
| 2026-04-25 | 150 | +50.0% |
| 2026-05-02 | 300 | +100.0% |
```

```bash
$ devrel analytics history blog/cli-launch --format json
[
  {"period_end": "2026-04-18T00:00:00+00:00", "primary_metric": 100.0,
   "metric_name": "page_views", "content_type": "blog"},
  ...
]
```

Exits with code `1` if the `content_id` has no rows in `metric_history`.

---

## `devrel analytics diff`

Compare two reports side-by-side. Sorts by absolute %-delta. Surfaces top movers, plus `new` (only in B) and `gone` (only in A) classifications.

### Synopsis

```bash
devrel analytics diff PERIOD_A PERIOD_B [--format md|json] [--limit N]
```

### What it does

Periods are matched against `metric_history.period_end` with prefix matching: `2026-04-25` matches any timestamp starting with that date. Pass full ISO timestamps if you have multiple reports per day.

### Examples

```bash
$ devrel analytics diff 2026-04-25 2026-05-02
# Diff: 2026-04-25 → 2026-05-02

| content_id | kind | a | b | delta |
|---|---|---|---|---|
| blog/big-mover | changed | 100 | 500 | +400.0% |
| blog/new | new | — | 200 | — |
| blog/gone | gone | 80 | — | — |
| blog/flat | changed | 50 | 51 | +2.0% |
```

```bash
devrel analytics diff 2026-04-25 2026-05-02 --limit 5 --format json
```

Exits with code `1` if either period has no rows.

---

## `devrel analytics calibration`

Score how well past recommendations actually panned out.

### Synopsis

```bash
devrel analytics calibration [--format md|json]
```

### What it does

For each historical recommendation that has at least one `metric_history` row strictly after its `first_seen_period`, decides whether the action's prediction held. Currently scores only `double_down` and `retire`:

- `double_down` "held" if the average post-period metric for source content is ≥ 90% of the value at `first_seen_period`.
- `retire` "held" if the max post-period metric is ≤ 110% of the anchor (no recovery).

Other actions (`rewrite`, `amplify`, `retest`, `investigate`) are counted as unscored — they don't have a clean post-hoc test without human input on what was acted on.

### Output

```bash
$ devrel analytics calibration
# Argus calibration

- scored recommendations: **12**
- unscored (insufficient post-period data or non-scoreable action): 8
- high-confidence (≥0.8) hit rate: 75%
- low-confidence (<0.5) hit rate: 40%

| action | n | panned_out | rate | avg_conf | lift vs coin-flip |
|---|---|---|---|---|---|
| double_down | 7 | 6 | 86% | 0.81 | +0.36 |
| retire | 5 | 4 | 80% | 0.74 | +0.30 |
```

`lift vs coin-flip` is `rate − 0.5` — positive means Argus does better than chance for that action. A consistently negative lift means treat that action class with suspicion.

### When to run it

After ≥ 2 weekly cycles, calibration starts to be informative. After ~4-6 cycles you have enough data to decide whether Argus's `confidence` field is well-calibrated for your project. If high-confidence and low-confidence hit rates are similar, the model isn't differentiating — consider tightening the prompt or accepting that the absolute number means little.

---

## `devrel analytics summary`

Cross-project rollup. Walks every `.devrel/state.db` under `--root` and aggregates totals per project.

### Synopsis

```bash
devrel analytics summary [--root PATH] [--format md|json] [--max-depth N]
```

### Options

- `--root PATH` — directory to scan. Default: `~`. Use a tighter root for faster scans.
- `--max-depth N` — max directory depth to descend. Default: `4`. Skips dot-directories (other than `.devrel`) so `~/.cache`, `~/.config`, etc. don't slow the walk to a crawl.
- `--format md|json` — output format. Default: `md`.

### What it does

For each `.devrel/state.db` found:

- `last_report` — most recent `period_end` from `analytics_reports`
- `total_recs` — count of rows in `analytics_recommendations`
- `total_metrics` — count of rows in `metric_history`
- `spend_usd` — `SUM(cost_usd)` from `costs WHERE agent = 'argus'`

### Example

```bash
$ devrel analytics summary --root ~/projects
# Argus cross-project summary — 3 projects under /Users/me/projects

| project | last_report | total_recs | total_metrics | spend_usd |
|---|---|---|---|---|
| /Users/me/projects/openclaw | 2026-05-02 | 24 | 412 | $1.92 |
| /Users/me/projects/tiketti | 2026-04-30 | 8 | 87 | $0.48 |
| /Users/me/projects/sandbox | — | 0 | 0 | $0.00 |
```

For consultants or solo founders running `devrel-origin` across multiple products, this is the closest thing to a portfolio dashboard without a hosted backend (which the CLI design rules out).

---

## Schedules

Any of these verbs can be installed as a cron entry via the existing scheduler:

```bash
devrel schedule install "analytics report --push" --weekly
devrel schedule list
devrel schedule remove
```

A typical weekly setup: `analytics report --push` runs Friday morning so a stakeholder digest lands before the Monday standup.

## See also

- [`docs/agents/argus.md`](../agents/argus.md) — agent reference, schemas, action vocabulary
- [`docs/cookbook.md`](../cookbook.md) — recipes including the calibration loop and multi-project workflows
- [`docs/quickstart.md`](../quickstart.md) — bootstrap + first weekly cycle
