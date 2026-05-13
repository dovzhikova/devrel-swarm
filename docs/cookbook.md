# Cookbook

Recipes for getting useful work out of `devrel-origin`. Each recipe is short and self-contained — copy, paste, adapt.

## 1. Run a weekly cycle on cron

```bash
# install: Mondays 09:00 UTC, full pipeline
devrel schedule install --expression "0 9 * * 1"

# verify it landed
devrel schedule list

# remove it later
devrel schedule remove
```

The install writes to your user crontab and includes the project's `.devrel/` path so the cron entry runs the same `devrel run` that you'd run interactively. It does not run as root.

## 2. Schedule analytics-only on a separate cadence

The full weekly cycle is content-heavy. If you want analytics more often (or earlier in the week), wrap the verb directly:

```bash
devrel schedule install "analytics report --push" --expression "0 7 * * 5"
```

Now the analytics digest lands Friday at 07:00 UTC, separate from the Monday content drop.

## 3. Set up the editorial contract

The four committed files in `.devrel/` are the editorial contract. Treat them like source code.

```bash
# voice — three to five short samples of your best published content
$EDITOR .devrel/voice.md

# style — readability targets, structural rules
$EDITOR .devrel/style.md

# slop blocklist — phrases that must never appear
$EDITOR .devrel/slop-blocklist.md

# config — product identity, model selection, budget caps
$EDITOR .devrel/config.toml

git add .devrel/voice.md .devrel/style.md .devrel/slop-blocklist.md .devrel/config.toml
git commit -m "chore: editorial contract for myproduct"
```

Subsequent cycles will use these files; changes get tracked in git. If a draft drifts off-voice, change the sample passages, not the prompt.

## 4. Audit an existing draft (no LLM call until pass 4)

```bash
devrel content audit ./drafts/blog-post.md --type blog_post
```

Runs the existing draft through the editorial pipeline without re-generation. Cheaper than a full draft because the developmental/line/copy edit stages are critique-only.

## 5. Disable Argus inside `devrel run`

Argus runs as Stage 5b by default, costing ~$0.03 per cycle. To disable:

```toml
# .devrel/config.toml
[orchestration]
analytics_in_run = false
```

You can still run Argus ad-hoc:

```bash
devrel analytics report --since 7d
```

## 6. Calibrate before trusting recommendations

After 4-6 weekly cycles:

```bash
devrel analytics calibration
```

Look at two numbers:

- **`lift vs coin-flip`** per action — positive means Argus does better than chance for that action class.
- **High-confidence vs low-confidence hit rate** — if they're similar, the model isn't differentiating; the absolute confidence values are noise.

Decisions:

- **High lift, well-differentiated**: trust `confidence ≥ 0.8` recommendations enough to act on them without much review.
- **Low lift, poorly-differentiated**: treat all recommendations as discussion prompts, not directives. Consider tightening `optimize/argus/system_prompt.txt`.
- **Negative lift on a specific action**: that class is consistently wrong. Investigate before acting on any rec with that action.

## 7. Drill into a single piece

```bash
devrel analytics history blog/cli-launch
```

Shows every recorded period for one piece. Useful when a recommendation says "retire blog/x" and you want to see whether x has actually trended down or had one bad week.

## 8. Compare two reports

```bash
devrel analytics diff 2026-04-25 2026-05-02 --limit 10
```

Top movers between two periods. Reading order: biggest gainers and losers first, then `new` (fresh content) and `gone` (content that dropped out of the window).

## 9. Cross-project portfolio view

If you run `devrel-origin` across multiple products:

```bash
devrel analytics summary --root ~/projects
```

Shows total spend per project, last report period, and recommendation counts. The closest thing to a portfolio dashboard without a hosted backend.

## 10. Hand a recommendation to Mox or Kai

When Argus emits an actionable recommendation (`double_down`, `amplify`, or `rewrite`), it stages a brief at `.devrel/deliverables/argus-brief-<period>-<action>-<target>.md`. The brief carries the rationale, evidence, source IDs, and a tailored next-step shell command. Open the brief, review the evidence, and run the suggested command:

```bash
devrel deliverables list | grep argus-brief
cat .devrel/deliverables/argus-brief-2026-05-02-double_down-theme-python.md
# review, then run the suggested command from the brief's "Next step" section
```

This is the bridge between B-mode (recommend) and a future closed-loop C-mode without committing to autonomous execution.

## 11. Override an agent's prompt

If the default Argus or Sentinel prompt isn't quite right for your project:

```bash
mkdir -p optimize/argus
# Drop your own prompt at optimize/argus/system_prompt.txt; it overrides the default
$EDITOR optimize/argus/system_prompt.txt
```

The agent auto-detects the file via `load_agent_prompt`. No code changes needed. Same pattern works for `optimize/kai`, `optimize/mox`, etc.

## 12. Cap spend hard

```toml
# .devrel/config.toml
[budget]
weekly_usd_cap = 5.0
```

When hit, BudgetGate forces all subsequent calls to Haiku regardless of the agent's preferred model. Usage stays bounded; you don't lose coverage.

```bash
devrel cost                    # spend so far this week
devrel cost --month 2026-05    # full month
```

## 13. Recovery: weekly cycle crashed mid-stage

```bash
devrel run                     # resumes from the last checkpoint
```

`Atlas.run_weekly_cycle` writes a per-stage checkpoint to `.devrel/state.db`. On the next invocation, completed stages are skipped and only the failed/pending ones re-run. To force a fresh start, clear checkpoints from the state DB or wait for the next scheduled run.

## 14. Echo's social_mentions table moved

If you upgrade Echo and the `social_mentions` schema changes, `SocialCollector` will warn:

```text
SocialCollector: Echo's social_mentions table is missing required columns: ['engagement_score']. Argus will return no social metrics until the schema is updated.
```

Either roll back the Echo version or rename the column back. The contract is documented at the top of `src/devrel_origin/tools/analytics.py` (`SocialCollector._REQUIRED_COLUMNS`).

## 15. Test the system before pointing it at production

```bash
.venv/bin/pytest tests/ -q -o addopts=""
```

Expected baseline: **800 passing / 21 baseline failing** (the 21 are pre-existing infrastructure tests that fail without external API credentials — unrelated to `devrel-origin`'s logic). New work should not change those numbers.

## See also

- [`docs/quickstart.md`](quickstart.md) — install + bootstrap
- [`docs/agents/argus.md`](agents/argus.md) — Argus deep dive
- [`docs/cli/analytics.md`](cli/analytics.md) — analytics verbs reference
- [`README.md`](../README.md) — full CLI surface and architecture
