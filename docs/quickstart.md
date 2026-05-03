# Quickstart

Bootstrap a project, scaffold `.devrel/`, and run the first weekly cycle in 5 minutes.

This guide assumes you already have `pipx` and Python 3.12+. If you don't, install [pipx](https://pipx.pypa.io/) first.

## 1. Install

```bash
pipx install devrel-swarm
```

Verify:

```bash
devrel --version
```

## 2. Bootstrap a project

`devrel-swarm` operates on the current working directory the way `git` does. Cd into the project repo you want to apply it to (or any new directory) and run:

```bash
cd /path/to/your/project
devrel init \
  --name myproduct \
  --url https://myproduct.dev \
  --github-repo me/myproduct
```

Output (this is the actual scaffold the CLI produces):

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

Done. Edit voice.md / style.md / slop-blocklist.md, then run devrel doctor.
```

Four files are intended to be committed: `config.toml`, `voice.md`, `style.md`, `slop-blocklist.md`. They encode the editorial contract — diff and review them like any other source. Everything else is gitignored.

## 3. Tell the system what your voice sounds like

Open `.devrel/voice.md` and replace the placeholders with three to five short sample passages from your existing best-published content. The Sentinel and persona-pass agents use this to keep new content sounding like you, not like generic AI prose.

Open `.devrel/style.md` and adjust the per-content-type readability targets if your house style differs from the defaults.

Open `.devrel/slop-blocklist.md` and add any phrases that should never appear in your content.

## 4. Set required env vars

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Optionally set the integrations you need:

```bash
export GITHUB_TOKEN=ghp_...        # for Sage's issue triage
export FIRECRAWL_API_KEY=fc-...    # for Echo's social listening + KB harvesting
export POSTHOG_API_KEY=phc_...     # for Argus's analytics
export INSTANTLY_API_KEY=...       # for Pax's email automation
```

## 5. Run a health check

```bash
devrel doctor
```

Real output from a fresh scaffold:

```text
✓ python_version           3.13.12
✓ config.toml              
✓ voice.md                 
✓ style.md                 
✓ slop-blocklist.md        
✓ config_parses            project=myproduct
✓ state_db                 schema v4
✗ ANTHROPIC_API_KEY        not set (required)
! GITHUB_TOKEN             not set (optional)
! FIRECRAWL_API_KEY        not set (optional)
...
! kb_files                 0 markdown files
```

`✓` = ready, `!` = optional and missing, `✗` = required and missing. Fix the `✗` lines before running anything that talks to an LLM.

## 6. Try the easy wins

These verbs work without any external API keys — they read local state.

```bash
devrel cost                # spend report from .devrel/state.db
devrel deliverables list   # generated outputs
devrel config get project  # read config.toml values
```

## 7. First content draft

```bash
devrel content draft "tutorial on feature flags" --type tutorial
```

This routes through the 8-stage editorial pipeline:

```text
developmental edit → line edit → copy edit
  → anti-slop pass (regex + LLM lint, force-rewrite once or AbortLoud)
    → reader-persona test (skeptical-dev scorer)
      → readability check (Flesch-Kincaid + sentence stats)
        → re-loop into copy edit once on persona/readability fail
          → final draft + revision-trace.json
```

The output lands in `.devrel/deliverables/`. The trace JSON has every stage's score and diff so you can audit the pipeline.

## 8. Schedule the weekly cycle

```bash
devrel run                 # full weekly orchestration, ad-hoc
```

Or install a cron entry:

```bash
devrel schedule install    # default: Mondays 09:00 UTC
devrel schedule list
```

The weekly cycle runs all 13 agents in parallel waves (see [README.md](../README.md#how-it-works-internally)) and ends with a digest pushed to your configured channels.

## 9. Look at performance after a few weeks

After two or more weekly cycles have run, Argus has enough metric history to be useful:

```bash
devrel analytics report --since 7d   # ad-hoc report
devrel analytics history blog/x      # one piece's trajectory
devrel analytics calibration         # how well past recs panned out
```

See [`docs/cli/analytics.md`](cli/analytics.md) for the full surface.

## What's next

- [`docs/cookbook.md`](cookbook.md) — common recipes (calibration, weekly cron, multi-project rollup)
- [`docs/agents/argus.md`](agents/argus.md) — deep dive on the post-publish analyst
- [`README.md`](../README.md) — full CLI reference and architecture
