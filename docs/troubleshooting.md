# Troubleshooting

Common failures and their fixes. Anchored on issues that have actually
bitten users in the wild, not hypothetical ones.

If your problem isn't here, run `devrel doctor --json` and open an
issue at <https://github.com/dovzhikova/devrel-origin/issues> with the
output attached.

---

## Setup and auth

### `OpenRouter 400 Bad Request` on `devrel auth` or `devrel run`

**Symptom:** `devrel auth --provider openrouter` validation fails with
a 400, or `devrel run` errors out with a 400 from the OpenRouter
chat-completions endpoint.

**Cause:** Versions ≤ 0.2.10 hardcoded the OpenRouter default model
ids in Anthropic's dated format
(`anthropic/claude-sonnet-4-5-20250929`), which OpenRouter does not
accept. OpenRouter uses dot notation without a date suffix
(`anthropic/claude-sonnet-4.5`).

**Fix:** Upgrade to v0.2.11 or later:

```bash
pipx upgrade devrel-origin
devrel --version  # should print 0.2.11+
```

If you're stuck on an older version for another reason, override the
default model per-call (env var or `.devrel/config.toml` `[models]`
section) with a real OpenRouter id. Check
<https://openrouter.ai/models?providers=anthropic> for the current
list.

### `ANTHROPIC_API_KEY is required`

**Symptom:** Any LLM-touching verb (`devrel run`, `devrel content
draft`, etc.) exits with `ANTHROPIC_API_KEY is required (or set
OPENROUTER_API_KEY...)`.

**Fix:** Run `devrel auth`. The interactive picker walks you through
both Anthropic and OpenRouter setup, validates the key, and writes it
to `.devrel/.env` with `chmod 600`. All subsequent verbs auto-load
the file; you don't need to `export` anything in your shell.

```bash
devrel auth                          # interactive
devrel auth --provider openrouter    # skip the provider prompt
devrel auth --rotate                 # replace an existing key
```

### `devrel auth` validation hangs or times out

**Symptom:** The validation ping after pasting the key never
completes, or fails with a network error.

**Fix:** Pass `--no-validate` to skip the ping (useful on
metered/offline keys or behind a corporate proxy):

```bash
devrel auth --provider openrouter --key sk-or-... --no-validate
```

The key is written to `.devrel/.env` regardless. Run a real verb
afterward to surface any auth issue.

### `devrel doctor` says `state_db schema mismatch`

**Symptom:** `! state_db   schema vN, current is vM; run devrel migrate`

**Fix:**

```bash
devrel migrate
```

This is non-destructive: schema upgrades only add tables/columns,
never drop data.

---

## Content generation

### `Kai did not produce content (status=insufficient_evidence)`

**Symptom:** `devrel content draft "..."` exits with
`Kai did not produce content (status=insufficient_evidence)` and lists
gaps like `no knowledge-base, official-docs, or repository evidence`.

**Cause:** Kai refuses to silently produce ungrounded content. Your
KB is empty, your prompt asks for things upstream agents didn't
provide (pain points, GitHub issues, file paths), or you have no
SearchTools configured to fetch official docs.

**Fix:** Populate the KB first, then re-run.

```bash
# Harvest from a docs site (Firecrawl required)
devrel kb add https://docs.myproduct.dev

# Or drop markdown files manually
cp ~/notes/architecture.md .devrel/kb/

# Verify
devrel kb list
devrel content draft "tutorial on feature flags" --type tutorial
```

If your prompt mentions pain points or GitHub issues, run the relevant
upstream agents first or provide them via context. The simplest path
is `devrel run`, which runs Sage and Iris before Kai so the
content_brief has real signal.

### `blocked_by_quality_gate` / `AbortLoud: slop persisted`

**Symptom:** `devrel content draft` exits nonzero with
`status=blocked_by_quality_gate` or the editorial pipeline raises
`AbortLoud: slop persisted: <phrase>`.

**Cause:** The anti-slop pass ran a force-rewrite and the same
blocked phrase still appeared. This is intentional in v0.2.10+: the
pipeline propagates the abort instead of silently shipping a weaker
single-revision draft.

**Fix:** Inspect the blocked phrase. If it's a real false positive
for your domain, remove it from `.devrel/slop-blocklist.md` and
re-run. If it's correctly blocked but the model keeps inserting it,
edit `.devrel/voice.md` to add an explicit "do not use" example.

```bash
$EDITOR .devrel/slop-blocklist.md
$EDITOR .devrel/voice.md
devrel content draft "..." --type tutorial
```

### `Code validation: N/M blocks failed syntax checks`

**Symptom:** Draft ships but you see
`⚠ Code validation: 2/3 blocks failed syntax checks` in the output.

**Cause:** Kai's draft contains code blocks (Python, JSON, YAML,
shell, JS) that fail syntax validation. The draft is still written
to `.devrel/deliverables/` so you can review.

**Fix:** Inspect `<deliverable>-trace.json` for the `code_validation`
section. Common causes:

- Hallucinated APIs / methods that don't exist (model needs better
  KB grounding)
- Outdated GitHub Actions (e.g. `actions/upload-artifact@v3` is
  flagged as deprecated)
- Truncated multi-line shell with an unclosed quote

If the model keeps hallucinating, populate the KB with the real API
reference and re-run.

### Sentinel `audit_failed` with `overall_score: 0`

**Symptom:** Sentinel returns `status=audit_failed`, score 0, and a
recommendation to retry.

**Cause:** v0.2.10+ no longer falls back to structural audit when the
LLM returns malformed JSON. The model's response was unparseable.

**Fix:** Re-run the cycle (`devrel run --agent sentinel ...`). If the
failure is consistent, your model is drifting on JSON output;
consider switching to a stronger model in `.devrel/config.toml`
`[models]` section, or check if you're using a heavily-quantized
OpenRouter route.

### `evidence_gaps: task requires repository file paths, but Dex provided none`

**Symptom:** Kai short-circuits with the above gap.

**Cause:** Your prompt mentions "file paths" or "source code" but
`dex_docs` is empty in the SharedContext. This usually means you're
running Kai standalone (`devrel content draft`) without first running
Dex (`devrel run` does this automatically).

**Fix:** Either run the full cycle (`devrel run`) which threads Dex
output to Kai, or rephrase the prompt to not require file paths.

---

## Argus / analytics

### `PostHogCollector failed: ...`

**Symptom:** Argus run logs `PostHogCollector failed: <error>` and
`sources_ok["posthog"] = False` in the report.

**Common causes:**

- `POSTHOG_API_KEY` or `POSTHOG_PROJECT_ID` missing → set them
- Wrong key type → use a Personal API key, not an Insight key
- Stale `PostHogClient` method (`fetch_events_by_url`) → upgrade to
  v0.2.10+ which adds the adapter

### Argus calibration says `not enough data`

**Symptom:** `devrel analytics calibration` returns
`{"status": "insufficient_history"}`.

**Cause:** Need at least 2 weekly Argus runs with content overlap to
compute calibration.

**Fix:** Wait. Or backfill manually if you have analytics history
elsewhere.

---

## Schedule / cron

### `devrel schedule install` fails on macOS with `1 (Operation not permitted)`

**Symptom:** Cron install errors with permission denied.

**Cause:** macOS requires the terminal app to have Full Disk Access
to install crontab entries.

**Fix:** System Settings → Privacy & Security → Full Disk Access →
add Terminal (or iTerm). Re-run.

---

## Cost / budget

### `BudgetGate: exceeded weekly_usd_cap, downgrading to haiku`

**Symptom:** Atlas logs the BudgetGate forcing Haiku for the rest of
the cycle.

**Cause:** Working as designed. `[orchestration].weekly_usd_cap` in
`.devrel/config.toml` is your soft ceiling; once exceeded, all agents
downgrade to the cheap model.

**Fix:** Either raise the cap, switch to OpenRouter (per-token
billing is often cheaper than Anthropic direct on Sonnet), or accept
the Haiku output.

---

## Reporting a new issue

Open an issue at <https://github.com/dovzhikova/devrel-origin/issues>
with:

1. `devrel --version`
2. `devrel doctor --json`
3. The exact command you ran
4. Full error output (sanitize any keys first)

If the failure is in a specific agent's output, attach the relevant
file from `.devrel/deliverables/<week>/` and the matching `.trace.json`.
