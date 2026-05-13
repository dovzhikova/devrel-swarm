# Quickstart

Bootstrap a project, configure an LLM key, and produce your first
grounded content draft in under five minutes.

`devrel init` is an interactive wizard that walks you through every step:
scaffold → LLM key → health check → voice tuning → first draft. The rest
of this doc explains what each step does and how to recover if you skip
or fail one.

This guide assumes you already have `pipx` and Python 3.12+. If you
don't, install [pipx](https://pipx.pypa.io/) first.

## TL;DR

```bash
pipx install devrel-origin
cd /path/to/your/project
devrel init             # interactive wizard, scaffold through first draft
```

That's it. Read on if you want to know what the wizard is doing, or if
you want to run the steps manually instead.

## 1. Install

```bash
pipx install devrel-origin
devrel --version
```

## 2. Bootstrap a project

`devrel-origin` operates on the current working directory the way `git`
does. `cd` into the project repo you want to apply it to (or any new
directory) and run:

```bash
cd /path/to/your/project
devrel init             # interactive: prompts for name, url, github-repo
                        # then chains into the onboarding wizard
```

For CI / scripts that want scaffold without the wizard:

```bash
devrel init \
  --non-interactive \
  --name myproduct \
  --url https://myproduct.dev \
  --github-repo me/myproduct
```

Either way you'll see the scaffold land in `.devrel/`:

```text
+ .devrel/
+ kb/
+ deliverables/
+ context/
+ config.toml
+ voice.md
+ style.md
+ slop-blocklist.md
+ .gitignore
+ state.db

Done. Next steps:
  1. Run devrel auth to configure your LLM API key (Anthropic or OpenRouter).
  2. Edit voice.md / style.md / slop-blocklist.md to match your project's voice.
  3. Run devrel doctor to verify everything is wired up.
```

Four files are intended to be committed: `config.toml`, `voice.md`,
`style.md`, `slop-blocklist.md`. They encode the editorial contract.
Diff and review them like any other source. Everything else is
gitignored (deliverables, state DB, KB, context archive).

## 3. Configure an LLM key

```bash
devrel auth
```

`devrel auth` is an interactive picker. It asks which provider you
want, takes the key with hidden input, validates it with a one-token
ping, and writes it to `.devrel/.env` with `chmod 600`. Subsequent
commands auto-load the file: no need to `export` anything in your
shell.

**Recommended: OpenRouter.** Lower onboarding barrier than Anthropic
API direct (no waitlist, free monthly credits to try the system out)
and supports per-agent model routing later when you want to tune cost.
Sign up at <https://openrouter.ai/keys> and pick option 2 in the
prompt.

If you prefer Anthropic direct (for cache hits, deeper rate limits, or
billing reasons), pick option 1 and use a key from
<https://console.anthropic.com/settings/keys>.

You can switch providers later or rotate the key:

```bash
devrel auth --provider openrouter --rotate
devrel auth --provider anthropic --key sk-ant-... --no-validate  # CI shape
```

Optionally configure other integrations as you need them. None are
required to produce a first draft:

```bash
# These also live in .devrel/.env (or your shell profile if you prefer)
GITHUB_TOKEN=ghp_...        # Sage's issue triage, Argus's repo metrics
FIRECRAWL_API_KEY=fc-...    # Echo's social listening, KB harvester
POSTHOG_API_KEY=phc_...     # Argus's analytics
INSTANTLY_API_KEY=...       # Pax's email automation
APOLLO_API_KEY=...          # Rex's contact enrichment
```

## 4. Run a health check

```bash
devrel doctor
```

```text
✓ python_version           3.13.12
✓ config.toml
✓ voice.md
✓ style.md
✓ slop-blocklist.md
✓ config_parses            project=myproduct
✓ state_db                 schema v4
✓ llm_api_key              set: OPENROUTER_API_KEY
! GITHUB_TOKEN             not set (optional)
! FIRECRAWL_API_KEY        not set (optional)
...
! kb_files                 0 markdown files
```

`✓` = ready, `!` = optional and missing, `✗` = required and missing.
Each `✗` line names the fix verb (e.g. `run devrel auth`). Fix the `✗`
lines before running anything that talks to an LLM.

## 5. First content draft

```bash
devrel content draft "tutorial on feature flags" --type tutorial
```

This routes through Kai (the content agent) which:

1. Searches `.devrel/kb/` (TF-IDF) for relevant grounding docs
2. Optionally fetches official docs via SearchTools (if Firecrawl/Brave wired)
3. Builds a grounded prompt with KB excerpts + repo context
4. Runs the 8-stage editorial pipeline:

```text
developmental edit → line edit → copy edit
  → anti-slop pass (regex + LLM lint, force-rewrite once or AbortLoud)
    → reader-persona test (skeptical-dev scorer)
      → readability check (Flesch-Kincaid + sentence stats)
        → re-loop into copy edit once on persona/readability fail
          → final draft + revision-trace.json
```

5. Validates code blocks (Python AST, JSON, YAML, shell parse, JS delimiters)

The draft lands in `.devrel/deliverables/<timestamp>-tutorial-on-feature-flags.md`.
The matching `*-trace.json` carries `grounding_sources`,
`code_validation`, `revision`, and `pain_points_addressed` so you can
audit every decision the pipeline made.

> **Empty KB?** The first draft will warn `No KB sources matched the
> prompt`. The pipeline still produces output, but it's ungrounded.
> Populate `.devrel/kb/` with `devrel kb add <url>` (Firecrawl) or
> drop markdown files in `.devrel/kb/` directly. See
> [docs/troubleshooting.md](troubleshooting.md#empty-kb).

## 6. Tune voice, style, and slop

The first draft uses the shipped templates for `voice.md`, `style.md`,
`slop-blocklist.md`. They're generic on purpose: the system has to
boot with no input. To make output sound like *you* (or your product),
edit those three files:

- `.devrel/voice.md` — three to five short sample passages from your
  best published content. Sentinel and the persona pass use this to
  detect drift.
- `.devrel/style.md` — per-content-type readability targets. Adjust if
  your house style differs from defaults.
- `.devrel/slop-blocklist.md` — banned phrases. The anti-slop pass
  runs both regex and LLM lint against this list. Add patterns you
  see in the first draft that you wouldn't ship.

Re-run the same draft command. The output should now sound like the
voice samples and avoid the blocked phrases:

```bash
devrel content draft "tutorial on feature flags" --type tutorial
```

## 7. Run the full weekly cycle

When you're ready to run all 13 agents (community triage, social
listening, theme synthesis, growth experiments, Kai content, video,
Dex docs, competitive intel, sales outreach, campaigns, brand audit,
post-publish analytics):

```bash
devrel run                 # ad-hoc, runs once
```

Or schedule it:

```bash
devrel schedule install    # default: Mondays 09:00 UTC
devrel schedule list
```

The weekly cycle runs agents in parallel waves
(see [README.md](../README.md#how-it-works-internally)) and ends with
a digest pushed to your configured channels (Telegram, email, Sheets).

## 8. Look at performance after a few weeks

After two or more weekly cycles, Argus has metric history:

```bash
devrel analytics report --since 7d   # ad-hoc report
devrel analytics history blog/x      # one piece's trajectory
devrel analytics calibration         # how well past recs panned out
```

See [`docs/cli/analytics.md`](cli/analytics.md) for the full surface.

## What's next

- [`docs/troubleshooting.md`](troubleshooting.md) — common failures and fixes
- [`docs/cookbook.md`](cookbook.md) — recipes (calibration, weekly cron, multi-project rollup)
- [`docs/agents/argus.md`](agents/argus.md) — deep dive on the post-publish analyst
- [`README.md`](../README.md) — full CLI reference and architecture
