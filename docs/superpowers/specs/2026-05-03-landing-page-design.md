# Landing page — design spec

**Status:** Approved (2026-05-03)
**Author:** Daria Dovzhikova
**Audience:** DevTools founders / small founding teams
**Deliverable:** Single self-contained `landing/index.html` with embedded CSS, no build step.

## Why this exists

`devrel-origin` ships at v0.2.4 with no landing page. The README is the de-facto front door, but it's structured for evaluation, not conversion. A new visitor (typically a founder, since that's who the CLI is built for) has to read past the architecture diagram before getting to "what does this do for me." A landing page closes that gap.

The page is one artifact. It is not a website — there is no blog, no team page, no docs (docs live in `docs/`), no email capture. It is a single scrollable index.html the founder lands on, scrolls, and either runs `pipx install` or doesn't.

## Audience and voice

**Primary reader:** DevTools founder, 1-5 person team, ships product full-time, can't yet justify a content/marketing hire, already comfortable with `git`/`npm`/`cargo` mental models.

**Voice:** matches the product's `voice.md` template — direct, technical, mildly irreverent, never preachy, no marketing fluff. Concrete numbers. The product has an anti-slop pass; the page passes its own anti-slop.

**Banned words and phrases:** revolutionary, game-changing, unleash, supercharge, leverage, AI-powered, reimagine, transform, "the future of", intelligent, cutting-edge, paradigm-shift, world-class, best-in-class. If any appear, the page is rejected.

## Visual direction

Developer/terminal aesthetic.

- Monospace typography for headlines (system stack: `'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace`)
- Sans-serif for body (system stack: `-apple-system, BlinkMacSystemFont, 'Inter', sans-serif`)
- Palette: near-black background `#0d0d0d`, off-white text `#e8e8e8`, single accent `#7cb342` (terminal green), muted secondary `#888`
- Light-theme variant via `prefers-color-scheme: light` (off-white bg, near-black text)
- One accent color only — no gradients, no rainbow accents
- Code snippets are visual furniture; they appear in the hero, the "shape" section, and the "real session" section
- No stock photos, no gradient blobs, no rounded illustrations, no icons beyond Unicode characters where useful
- Subtle scroll reveal (CSS only, no JS framework — `prefers-reduced-motion` honored)

## Sections

Single-scroll layout, ~700-900 words of body copy total.

### 1. Hero

- Headline: *"A 13-agent DevRel team that lives in your repo."*
- Subhead: one sentence — "Run content, sales, and analytics from a single CLI. Bring your own Anthropic key."
- Primary CTA: install command as a copy-on-click code block: `pipx install devrel-origin`
- Secondary CTA: GitHub link

### 2. The problem

Three concrete founder pains, one sentence each. Examples:

- "You ship product all week. Saturday morning you remember you haven't tweeted in a month."
- "Your last blog post was 'introducing v0.3' — three releases ago."
- "You know which competitor just raised but couldn't write a battlecard if your runway depended on it."

### 3. The shape — "git for DevRel"

- One sentence: "Operates on your repo the way `git` does. Bootstraps a `.devrel/` directory you commit to source."
- Real `devrel init` output captured from the actual CLI.
- One paragraph explaining the four committed files (`config.toml`, `voice.md`, `style.md`, `slop-blocklist.md`) — the editorial contract, diff-able like any source file.

### 4. The 13 agents

Three columns:

| Health | DevRel | Sales |
|---|---|---|
| Watchdog | Sage | Rex |
| Sentinel | Echo | Pax |
| Argus | Iris | Mox |
| | Nova | |
| | Kai | |
| | Vox | |
| | Dex | |

One line per agent — what it does. No fluff. Atlas (the orchestrator) named separately above the grid.

### 5. The editorial pipeline

The 8-stage quality pipeline as a visual flow:

```
draft → developmental edit → line edit → copy edit
  → anti-slop (regex + LLM lint, force-rewrite once)
    → reader-persona (skeptical-dev scorer)
      → readability (Flesch-Kincaid + sentence stats)
        → re-loop into copy edit if persona/readability fail
          → final draft + revision-trace.json
```

One paragraph: "Most AI marketing tools generate a draft and ship it. This one routes every piece through eight critique stages before it leaves the pipeline. The result reads like senior-editor work, not GPT slop."

### 6. Performance, not just production (Argus)

- Headline: "Argus tells you what's working."
- Three bullets: closed action vocabulary (`double_down`, `retire`, `rewrite`, etc.), calibration loop, week-over-week deltas via indexed time-series.
- Pull-quote: "Most AI marketing tools generate content. This one tells you which to retire."
- One concrete CLI example: `devrel analytics report --since 7d`

### 7. A real session

A full copyable terminal block:

```bash
$ pipx install devrel-origin
$ devrel init --name myproduct --url https://myproduct.dev --github-repo me/myproduct
$ export ANTHROPIC_API_KEY=sk-ant-...
$ devrel content draft "tutorial on feature flags" --type tutorial
$ devrel analytics report --since 7d --push
```

Annotation under the block: "That's the whole thing. No dashboard, no SaaS account, no SOC2 review."

### 8. What it costs

- "You bring your own Anthropic key."
- Concrete numbers:
  - Argus run: ~$0.03 per call (~$1.56/year on a weekly schedule)
  - Full `devrel run` weekly cycle: ~$2-4 per cycle depending on content volume
  - Budget cap configurable in `.devrel/config.toml`; BudgetGate forces Haiku when exceeded
- "No subscription. No seat licensing. No SaaS lock-in."

### 9. FAQ

Four or five short Q&As. Tentative list:

- *Does it lock me in?* No backend. The CLI runs locally. Your data is in `.devrel/state.db` and `knowledge_base/`. Walk away anytime.
- *Why is config in git?* Same reason your CI config is in git: editorial standards are decisions, and decisions belong in source.
- *What if I already use HubSpot / Buffer / Mailchimp?* Most won't replace those. `devrel-origin` produces the content; you can paste it where you already work.
- *Does it work without all the integrations?* Yes. Each integration (PostHog, GitHub, Instantly, Apollo, Telegram, Sheets) is optional. Missing keys cause graceful degradation, not crashes.
- *Is it production-ready?* 800 passing tests, multiple shipped versions, a 13-agent system maintained by one person. You should kick the tires and decide.

### 10. Install + footer

- Install command repeated as a copy-on-click block
- GitHub repo link
- MIT license badge / link
- Version (`v0.2.4`) — update on each release

## Tech and constraints

- Single file: `landing/index.html`
- All CSS embedded in `<style>` tag — no external stylesheets, no CSS frameworks
- Zero JavaScript dependencies. One ~30-line vanilla JS block for the copy-on-click behavior on code snippets, gated on `navigator.clipboard` availability with a graceful "select text" fallback
- Total page weight target: < 30 KB (HTML + inline CSS), no images
- Accessibility: WCAG AA contrast, semantic HTML (proper `h1`-`h3` hierarchy, `<main>`, `<section>`, `<nav>`), focus states, `prefers-reduced-motion` honored, `aria-label` on the copy buttons
- Mobile responsive: single breakpoint at 768px; cards reflow to single column below
- Light/dark theme via `prefers-color-scheme`

## Out of scope

- Multi-page site, blog, team page, careers page
- Email capture, signup form, "book a demo" CTA — there is no SaaS
- Build step (Webpack/Vite/etc.) — single static file
- Analytics tracking — out of scope for v1; can be added later by dropping a snippet into the `<head>`
- A/B variants — there is one version of the page
- Animation beyond CSS scroll reveal — no Framer Motion, no Lottie, no SVG animation
- Translations — English only

## Success criteria

- A founder lands on the page, scrolls once, and either copies the install command or closes the tab. No middle ground. The page does not waste anyone's time.
- The page passes the product's own anti-slop pass (no banned phrases, sentence-length and Flesch-Kincaid in the landing-page targets from `style.md`: 60-75 FK, mean sentence length 10-15 words).
- Lighthouse score: Performance ≥ 95, Accessibility ≥ 95, Best Practices ≥ 95, SEO ≥ 90.
- Page weight under 30 KB.
- Renders correctly with JS disabled (the copy-on-click degrades; everything else works).

## References

- Product `voice.md` template — `src/devrel_origin/project/templates/voice.md`
- Product `style.md` per-content-type targets — `src/devrel_origin/project/templates/style.md`
- Product `slop-blocklist.md` template — `src/devrel_origin/project/templates/slop-blocklist.md`
- Existing user docs — `docs/quickstart.md`, `docs/agents/argus.md`, `docs/cli/analytics.md`
- The Argus design spec is the format model — `docs/superpowers/specs/2026-05-02-argus-analytics-agent-design.md`
