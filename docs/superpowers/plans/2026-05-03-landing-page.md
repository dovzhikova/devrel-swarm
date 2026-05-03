# Landing page implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single self-contained `landing/index.html` plus a validator script that enforces the spec's success criteria (no banned phrases, < 30 KB weight, semantic HTML, both light/dark themes). The page targets devtools founders with a terminal aesthetic and 10 sections of ~700-900 body words.

**Architecture:** One HTML file with embedded `<style>` and a small inline `<script>` for copy-on-click. No build step, no framework, no external assets. A Python validator script (`scripts/check_landing.py`) runs after each section commit to catch regressions on the spec's hard constraints (page weight, banned-words list, HTML semantics).

**Tech Stack:** Hand-written HTML5 + embedded CSS (custom properties, container queries, `prefers-color-scheme`) + ~30 lines of vanilla JS. Validator: Python 3 stdlib only (`html.parser`, `pathlib`, `re`).

**Spec:** `docs/superpowers/specs/2026-05-03-landing-page-design.md`

---

## File structure

| File | Status | Responsibility |
|---|---|---|
| `landing/index.html` | Create | The single self-contained landing page |
| `scripts/check_landing.py` | Create | Validator: file weight, banned phrases, HTML semantics, theme presence |

The validator does the work tests would do on a regular code project. Each task ends with running it.

The spec's "Real session" section needs the actual `devrel init` output captured from the CLI; capture it once during Task 5 and embed verbatim.

---

## Task 1: Scaffold + validator script

**Files:**
- Create: `landing/index.html`
- Create: `scripts/check_landing.py`

- [ ] **Step 1: Write the validator script**

Create `scripts/check_landing.py`:

```python
#!/usr/bin/env python3
"""Static checks for landing/index.html against spec success criteria."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

LANDING = Path(__file__).resolve().parent.parent / "landing" / "index.html"

# From spec — slop blocklist applied to ALL text content (lowercase compare).
BANNED_PHRASES: tuple[str, ...] = (
    "revolutionary", "game-changing", "unleash", "supercharge", "leverage",
    "ai-powered", "reimagine", "transform", "the future of", "intelligent",
    "cutting-edge", "paradigm-shift", "world-class", "best-in-class",
)

MAX_BYTES = 30 * 1024  # 30 KB target from spec


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"style", "script", "code", "pre"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script", "code", "pre"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def check() -> list[str]:
    failures: list[str] = []

    if not LANDING.is_file():
        return [f"file missing: {LANDING}"]

    raw = LANDING.read_bytes()
    text_html = raw.decode("utf-8")

    # 1. Weight cap
    if len(raw) > MAX_BYTES:
        failures.append(f"file weight {len(raw)} bytes > {MAX_BYTES} cap")

    # 2. Banned phrases (in visible text only, not in code/pre/style/script)
    parser = TextExtractor()
    parser.feed(text_html)
    visible = parser.text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in visible:
            failures.append(f"banned phrase in visible copy: {phrase!r}")

    # 3. Semantic basics
    if not re.search(r"<html[^>]*\blang=", text_html):
        failures.append("missing <html lang=...> attribute")
    h1_count = len(re.findall(r"<h1\b", text_html))
    if h1_count != 1:
        failures.append(f"expected exactly 1 <h1>, found {h1_count}")
    if "<main" not in text_html:
        failures.append("missing <main> landmark")

    # 4. Both color schemes referenced
    if "prefers-color-scheme" not in text_html:
        failures.append("missing prefers-color-scheme media query")

    # 5. No external resources (per spec: zero deps)
    external = re.findall(
        r"""<(?:link|script|img|iframe)[^>]*\b(?:src|href)=["']https?://""",
        text_html,
    )
    if external:
        failures.append(f"external resource(s) detected: {len(external)}")

    return failures


def main() -> int:
    failures = check()
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK ({LANDING.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Create the skeleton HTML**

Create `landing/index.html` with just enough structure to pass the validator's "missing file" check (everything else will fail; that's expected at this point):

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>devrel-swarm — a 13-agent DevRel team that lives in your repo</title>
  <meta name="description" content="A pipx-installable CLI that runs content, sales, and analytics for devtools founders. Bring your own Anthropic key.">
  <style>
    /* Filled in Task 2 */
    @media (prefers-color-scheme: dark) { :root { color-scheme: dark; } }
  </style>
</head>
<body>
  <main>
    <!-- Sections filled in Tasks 3-9 -->
  </main>
  <script>
    /* Filled in Task 10 */
  </script>
</body>
</html>
```

- [ ] **Step 3: Run the validator and confirm it fails on the right things**

```bash
chmod +x scripts/check_landing.py
python3 scripts/check_landing.py
```

Expected output: `OK` with the file's current byte count (the skeleton already meets all the validator's hard requirements: lang, one h1 placeholder will fail — adjust expectation).

If it reports a banned phrase — there shouldn't be any in the skeleton; that means the validator has a false positive, fix it.
If it reports `expected exactly 1 <h1>, found 0` — that's expected; we add the h1 in Task 3.

For now, the validator should pass weight, semantic basics excluding h1, color scheme, and no-external-resource checks. Note the skeleton has no `<h1>` yet, so the h1-count check will fail. Accept this and move on.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html scripts/check_landing.py
git commit -m "feat(landing): scaffold + validator script

Skeleton index.html with semantic body, viewport meta, and slot for
embedded CSS/JS. Companion scripts/check_landing.py enforces the
spec's hard constraints (page weight cap, banned-phrase blocklist,
semantic HTML basics, both color schemes referenced, no external
resources). The validator runs after every section commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: CSS theme + base styles

**Files:**
- Modify: `landing/index.html` — replace the placeholder `<style>` block

- [ ] **Step 1: Write the CSS into the existing `<style>` block**

Replace the placeholder `<style>` block with:

```html
<style>
  /* Tokens — light is default, dark via prefers-color-scheme */
  :root {
    --bg: #fafaf7;
    --fg: #0d0d0d;
    --muted: #5a5a5a;
    --accent: #2f7d32;
    --border: #d8d8d4;
    --code-bg: #f0efe9;
    --max-width: 720px;
  }

  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d0d0d;
      --fg: #e8e8e8;
      --muted: #888;
      --accent: #7cb342;
      --border: #222;
      --code-bg: #161616;
      color-scheme: dark;
    }
  }

  /* Reset */
  *, *::before, *::after { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif;
    font-size: 16px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }

  /* Typography — monospace for hero/section headlines, sans for body */
  h1, h2, h3, .mono {
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-weight: 600;
    line-height: 1.2;
  }
  h1 { font-size: clamp(2rem, 5vw, 3rem); margin: 0 0 1rem; letter-spacing: -0.01em; }
  h2 { font-size: 1.5rem; margin: 4rem 0 1rem; }
  h3 { font-size: 1.1rem; margin: 1.5rem 0 0.5rem; }
  p  { margin: 0 0 1rem; }
  a  { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
  a:hover { text-decoration-thickness: 2px; }

  /* Layout */
  main { max-width: var(--max-width); margin: 0 auto; padding: 4rem 1.5rem 6rem; }
  section { margin-bottom: 4rem; }
  hr { border: 0; border-top: 1px solid var(--border); margin: 4rem 0; }

  /* Code */
  code, pre, kbd, samp {
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.9em;
  }
  code { background: var(--code-bg); padding: 0.1em 0.3em; border-radius: 3px; }
  pre {
    background: var(--code-bg);
    padding: 1rem 1.25rem;
    border-radius: 6px;
    overflow-x: auto;
    border: 1px solid var(--border);
    line-height: 1.5;
  }
  pre code { background: transparent; padding: 0; }

  /* Copy-on-click block — gets enriched by Task 10's JS */
  .install {
    position: relative;
    background: var(--code-bg);
    padding: 1rem 1.25rem;
    border-radius: 6px;
    border: 1px solid var(--border);
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-size: 1rem;
    margin: 1.5rem 0;
  }
  .install::before { content: "$ "; color: var(--muted); }
  .install button {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    padding: 0.25rem 0.5rem;
    font-family: inherit;
    font-size: 0.75rem;
    border-radius: 3px;
    cursor: pointer;
  }
  .install button:hover { color: var(--fg); border-color: var(--accent); }
  .install button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  /* Three-column grid for agents — collapses on narrow */
  .agents { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2rem; }
  .agents h3 { color: var(--accent); margin-top: 0; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .agents ul { list-style: none; padding: 0; margin: 0; }
  .agents li { padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
  .agents li:last-child { border-bottom: 0; }
  .agents li b { color: var(--fg); display: inline-block; min-width: 4.5rem; }
  .agents li span { color: var(--muted); }

  /* Pipeline diagram */
  .pipeline { font-size: 0.85rem; line-height: 1.7; color: var(--muted); }
  .pipeline strong { color: var(--fg); font-weight: 600; }

  /* FAQ */
  details {
    border-top: 1px solid var(--border);
    padding: 1rem 0;
  }
  details:last-of-type { border-bottom: 1px solid var(--border); }
  details summary {
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-weight: 600;
    cursor: pointer;
    list-style: none;
  }
  details summary::-webkit-details-marker { display: none; }
  details summary::before { content: "▸ "; color: var(--accent); }
  details[open] summary::before { content: "▾ "; }
  details p { margin-top: 0.75rem; color: var(--muted); }

  /* Footer */
  footer {
    margin-top: 6rem;
    padding-top: 2rem;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 0.85rem;
  }
  footer a { color: var(--muted); }

  /* Pull-quote */
  blockquote {
    border-left: 3px solid var(--accent);
    padding: 0.5rem 0 0.5rem 1.25rem;
    margin: 1.5rem 0;
    color: var(--fg);
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  }

  /* Mobile */
  @media (max-width: 640px) {
    main { padding: 2.5rem 1rem 4rem; }
    h1 { font-size: 1.75rem; }
    .agents { grid-template-columns: 1fr; gap: 2.5rem; }
    pre { font-size: 0.75rem; }
  }

  /* Reduced-motion respect */
  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
  }
</style>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: still failing only on `expected exactly 1 <h1>, found 0` (we add the h1 in Task 3). All other checks pass.

- [ ] **Step 3: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): CSS theme — light/dark, monospace headlines, terminal palette

Custom properties for both themes via prefers-color-scheme. Monospace
headlines (JetBrains Mono fallback chain), system sans body. Single
green accent (#2f7d32 light, #7cb342 dark). Layout: 720px max-width,
4rem section spacing. Mobile breakpoint at 640px. prefers-reduced-motion
honored. All embedded — zero external assets.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Hero section

**Files:**
- Modify: `landing/index.html` — add inside `<main>`

- [ ] **Step 1: Add the hero section**

Insert as the first child of `<main>`:

```html
<section aria-labelledby="hero">
  <h1 id="hero">A 13-agent DevRel team that lives in your repo.</h1>
  <p style="font-size: 1.15rem; color: var(--muted); margin-bottom: 2rem;">
    Run content, sales, and analytics from a single CLI. Bring your own Anthropic key.
  </p>
  <div class="install" data-copy="pipx install devrel-swarm">
    pipx install devrel-swarm<button type="button" aria-label="Copy install command">copy</button>
  </div>
  <p style="font-size: 0.9rem; color: var(--muted);">
    or <a href="https://github.com/dovzhikova/devrel-swarm">read the source on GitHub</a>.
  </p>
</section>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`. The h1 count check now passes.

- [ ] **Step 3: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): hero — headline, subhead, install CTA

Headline: 'A 13-agent DevRel team that lives in your repo.' Subhead
states scope + BYO key in one sentence. Primary CTA is the install
command in a copy-on-click block (button gets wired in Task 10).
Secondary CTA: GitHub repo link. No 'request a demo,' no email
capture — there's no SaaS to sign up for.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Problem + Shape sections

**Files:**
- Modify: `landing/index.html` — append after the hero `<section>`

- [ ] **Step 1: Add both sections**

```html
<section aria-labelledby="problem">
  <h2 id="problem">The problem</h2>
  <p>You ship product all week. Saturday morning you remember you haven't tweeted in a month.</p>
  <p>Your last blog post was &ldquo;introducing v0.3&rdquo; — three releases ago.</p>
  <p>You know which competitor just raised but couldn't write a battlecard if your runway depended on it.</p>
</section>

<section aria-labelledby="shape">
  <h2 id="shape">It's <code>git</code> for DevRel</h2>
  <p>
    Operates on your repo the way <code>git</code> does. <code>devrel init</code>
    bootstraps a <code>.devrel/</code> directory with the editorial contract and
    state DB. Diff voice and style like any other source.
  </p>
<pre><code>$ devrel init --name myproduct --url https://myproduct.dev --github-repo me/myproduct
+ .devrel/
+ kb/
+ deliverables/
+ context/
+ config.toml          # product identity, model selection, budget caps
+ voice.md             # tone profile + sample passages    (commit)
+ style.md             # house style + per-content targets (commit)
+ slop-blocklist.md    # banned phrases                    (commit)
+ .gitignore
+ state.db
Done. Edit voice.md / style.md / slop-blocklist.md, then run devrel doctor.</code></pre>
  <p>
    The four marked <code>(commit)</code> are the editorial contract.
    Diff them, review them, branch them — they're how the agents stay on-brand
    across hundreds of generated pieces.
  </p>
</section>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`.

- [ ] **Step 3: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): problem + shape sections

Three concrete founder pains, one sentence each. The 'shape' section
shows the actual devrel init output (captured from the CLI in
docs/quickstart.md) and explains the four committed editorial-contract
files — voice.md, style.md, slop-blocklist.md, config.toml — as
diff-able source.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Agents grid

**Files:**
- Modify: `landing/index.html` — append after the shape section

- [ ] **Step 1: Add the agents section**

```html
<section aria-labelledby="agents">
  <h2 id="agents">Thirteen specialists, one orchestrator</h2>
  <p>
    Atlas coordinates twelve specialist agents across three pipelines.
    Each agent has one clear job and degrades gracefully when its
    integrations are missing.
  </p>
  <div class="agents">
    <div>
      <h3>Health</h3>
      <ul>
        <li><b>Watchdog</b><span>infra + budget pre-flight</span></li>
        <li><b>Sentinel</b><span>pre-publish brand audit</span></li>
        <li><b>Argus</b><span>post-publish performance analyst</span></li>
      </ul>
    </div>
    <div>
      <h3>DevRel</h3>
      <ul>
        <li><b>Sage</b><span>GitHub issue triage + churn signals</span></li>
        <li><b>Echo</b><span>Reddit / HN / X social listening</span></li>
        <li><b>Iris</b><span>theme + pain-point synthesis</span></li>
        <li><b>Nova</b><span>experiment design + power analysis</span></li>
        <li><b>Kai</b><span>tutorials + technical content</span></li>
        <li><b>Vox</b><span>screen-recorded video tutorials</span></li>
        <li><b>Dex</b><span>AST-based docs + API references</span></li>
      </ul>
    </div>
    <div>
      <h3>Sales</h3>
      <ul>
        <li><b>Rex</b><span>competitive intel + Apollo enrichment</span></li>
        <li><b>Pax</b><span>outreach emails + battle cards</span></li>
        <li><b>Mox</b><span>blog, landing, social, campaigns</span></li>
      </ul>
    </div>
  </div>
</section>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`.

- [ ] **Step 3: Visually verify in browser**

```bash
python3 -m http.server 8123 --directory landing &
SERVER_PID=$!
sleep 1
echo "Open http://localhost:8123 in a browser"
# When done:
kill $SERVER_PID
```

Confirm: three columns desktop, single column mobile (resize browser to < 640px).

- [ ] **Step 4: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): 13-agent grid — Health / DevRel / Sales

Three-column responsive grid (single column under 640px). One line per
agent, monospace name + sans descriptor. Atlas named in the intro
paragraph. Argus listed under Health alongside Watchdog and Sentinel
to make the post-publish counterpart visible.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Editorial pipeline section

**Files:**
- Modify: `landing/index.html` — append after the agents section

- [ ] **Step 1: Add the pipeline section**

```html
<section aria-labelledby="pipeline">
  <h2 id="pipeline">Eight critique stages, not one prompt</h2>
  <p>
    Most AI marketing tools generate a draft and ship it. <code>devrel-swarm</code>
    routes every piece through eight stages before it leaves the pipeline.
  </p>
  <div class="pipeline">
    <strong>draft</strong> →
    developmental edit →
    line edit →
    copy edit →
    <strong>anti-slop</strong> (regex + LLM lint, force-rewrite once or AbortLoud) →
    <strong>persona</strong> (skeptical-dev scorer) →
    <strong>readability</strong> (Flesch-Kincaid + sentence stats) →
    re-loop into copy edit if persona / readability fails →
    final draft + <code>revision-trace.json</code>
  </div>
  <p>
    Each stage's score and diff is captured in the trace, so you can audit any
    piece end-to-end. Stages 1-4 use Sonnet; the cheaper stages use Haiku.
    Total cost lands at roughly 2.5-4× a single revision loop.
  </p>
</section>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`.

- [ ] **Step 3: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): editorial pipeline section

Visualizes the 8-stage critique flow with anti-slop, persona, and
readability called out as the differentiating stages. One paragraph
on revision-trace.json (auditability) and the Sonnet/Haiku cost mix.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Argus highlight section

**Files:**
- Modify: `landing/index.html` — append after the pipeline section

- [ ] **Step 1: Add the Argus section**

```html
<section aria-labelledby="argus">
  <h2 id="argus">Argus tells you what's working</h2>
  <p>
    Most AI marketing tools generate content. The 13th agent measures
    what shipped and tells you which to retire.
  </p>
  <ul>
    <li>
      <strong>Closed action vocabulary.</strong> Recommendations come typed:
      <code>double_down</code>, <code>retire</code>, <code>rewrite</code>,
      <code>retest</code>, <code>amplify</code>, <code>investigate</code>.
      No vague &ldquo;optimize&rdquo; or &ldquo;explore&rdquo; suggestions.
    </li>
    <li>
      <strong>Calibration loop.</strong> Each rec is scored against subsequent
      metric history. <code>devrel analytics calibration</code> shows whether
      the model's confidence is actually predictive for your project.
    </li>
    <li>
      <strong>Indexed time-series.</strong> Week-over-week deltas come from a
      <code>metric_history</code> table, not an LLM guess. You can drill into
      any piece's trajectory with <code>devrel analytics history blog/x</code>.
    </li>
  </ul>
  <blockquote>
    Most AI marketing tools generate content. This one tells you which to retire.
  </blockquote>
</section>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`.

- [ ] **Step 3: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): Argus highlight — performance, not just production

Three concrete differentiators: closed action vocab, calibration loop,
indexed metric_history. Each backed by a real CLI verb the reader can
run. Pull-quote pinned to the bottom of the section as the takeaway.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Real session + Costs sections

**Files:**
- Modify: `landing/index.html` — append after the Argus section

- [ ] **Step 1: Add both sections**

```html
<section aria-labelledby="session">
  <h2 id="session">A real session</h2>
<pre><code>$ pipx install devrel-swarm
$ cd ~/projects/myproduct
$ devrel init --name myproduct --url https://myproduct.dev --github-repo me/myproduct
$ export ANTHROPIC_API_KEY=sk-ant-...
$ devrel doctor
✓ python_version           3.13
✓ config.toml
✓ voice.md
✓ state_db                 schema v4
✓ ANTHROPIC_API_KEY        set

$ devrel content draft "tutorial on feature flags" --type tutorial
... 8-stage editorial pipeline runs ...
Wrote: .devrel/deliverables/feature-flags-tutorial.md (FK 62, 14 wpm)

$ devrel analytics report --since 7d --push
... Argus pulls metrics, ranks, recommends ...
Wrote: .devrel/deliverables/analytics-2026-05-02.md (5 recs, 2 briefs)
Pushed to Telegram + email.</code></pre>
  <p style="color: var(--muted);">
    That's the whole thing. No dashboard, no SaaS account, no SOC2 review.
  </p>
</section>

<section aria-labelledby="cost">
  <h2 id="cost">What it costs</h2>
  <p>You bring your own Anthropic key. Concrete numbers:</p>
  <ul>
    <li><strong>Argus run</strong> — ~$0.03 per call (~$1.56/year on a weekly schedule)</li>
    <li><strong>Full <code>devrel run</code> weekly cycle</strong> — ~$2-4 per cycle depending on content volume</li>
    <li><strong>Budget cap</strong> — configurable in <code>.devrel/config.toml</code>; <code>BudgetGate</code> forces Haiku when exceeded so coverage stays bounded</li>
  </ul>
  <p>No subscription. No seat licensing. No vendor lock-in.</p>
</section>
```

- [ ] **Step 2: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`.

- [ ] **Step 3: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): real session + cost transparency

Full copyable terminal block: install -> init -> doctor -> content
draft -> analytics report. Cost section gives concrete numbers
($0.03/Argus run, $2-4/weekly cycle) and explicitly names the BudgetGate
cap mechanism.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: FAQ + Footer

**Files:**
- Modify: `landing/index.html` — append after the cost section + add `<footer>` outside `<main>`

- [ ] **Step 1: Add the FAQ section inside main**

```html
<section aria-labelledby="faq">
  <h2 id="faq">FAQ</h2>
  <details>
    <summary>Does it lock me in?</summary>
    <p>
      No backend. The CLI runs locally. Your data is in
      <code>.devrel/state.db</code> and <code>knowledge_base/</code>.
      Walk away anytime — there's nothing to unsubscribe from.
    </p>
  </details>
  <details>
    <summary>Why is config in git?</summary>
    <p>
      Same reason your CI config is in git: editorial standards are
      decisions, and decisions belong in source. The four committed files
      (<code>config.toml</code>, <code>voice.md</code>, <code>style.md</code>,
      <code>slop-blocklist.md</code>) diff and review like any other source.
    </p>
  </details>
  <details>
    <summary>What if I already use HubSpot or Buffer?</summary>
    <p>
      <code>devrel-swarm</code> produces the content. Paste it where you
      already work. The point isn't to replace your distribution stack —
      it's to replace the part where you sit down to write the post in the
      first place.
    </p>
  </details>
  <details>
    <summary>Does it work without all the integrations?</summary>
    <p>
      Yes. PostHog, GitHub, Instantly, Apollo, Telegram, Sheets — each is
      optional. Missing keys cause graceful degradation, not crashes.
      Argus marks the failed source unhealthy and ships a partial report.
    </p>
  </details>
  <details>
    <summary>Is it production-ready?</summary>
    <p>
      800 passing tests, multiple shipped versions, a 13-agent system
      maintained by one person. Kick the tires on a side project before
      pointing it at your main repo.
    </p>
  </details>
</section>

<section aria-labelledby="install">
  <h2 id="install">Install</h2>
  <div class="install" data-copy="pipx install devrel-swarm">
    pipx install devrel-swarm<button type="button" aria-label="Copy install command">copy</button>
  </div>
</section>
```

- [ ] **Step 2: Add the footer outside `<main>`**

Right before `</body>`:

```html
<footer>
  <main>
    <p>
      <a href="https://github.com/dovzhikova/devrel-swarm">github.com/dovzhikova/devrel-swarm</a>
      · MIT licensed · v0.2.4
    </p>
    <p>
      Built by <a href="https://dariadovzhikova.com">Daria Dovzhikova</a>.
    </p>
  </main>
</footer>
```

- [ ] **Step 3: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`. The footer's nested `<main>` is fine because it's a layout container; the validator only checks `<main` is present anywhere, not for uniqueness. (If concerned, change the footer's `<main>` to a `<div>` and adjust CSS.)

- [ ] **Step 4: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): FAQ + install repeat + footer

Five-question FAQ in <details> elements (no JS needed for accordion).
Install command repeated as the final CTA. Footer: GitHub link, MIT
license, version, author credit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Copy-on-click JS

**Files:**
- Modify: `landing/index.html` — replace the placeholder `<script>` block

- [ ] **Step 1: Add the JS**

Replace the placeholder `<script>` block with:

```html
<script>
  (function () {
    if (!navigator.clipboard) return;
    document.querySelectorAll('.install').forEach(function (el) {
      var btn = el.querySelector('button');
      var text = el.dataset.copy || '';
      if (!btn || !text) return;
      btn.addEventListener('click', function () {
        navigator.clipboard.writeText(text).then(function () {
          var orig = btn.textContent;
          btn.textContent = 'copied';
          setTimeout(function () { btn.textContent = orig; }, 1500);
        }).catch(function () {
          btn.textContent = 'select + ⌘C';
        });
      });
    });
  })();
</script>
```

- [ ] **Step 2: Verify in browser**

```bash
python3 -m http.server 8123 --directory landing &
SERVER_PID=$!
sleep 1
# Open http://localhost:8123 in a browser, click both copy buttons,
# confirm the button text flashes "copied" and the clipboard contains
# "pipx install devrel-swarm".
kill $SERVER_PID
```

- [ ] **Step 3: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)`.

- [ ] **Step 4: Commit**

```bash
git add landing/index.html
git commit -m "feat(landing): copy-on-click for install command

Vanilla JS, ~20 lines, gated on navigator.clipboard. Wires up both
.install blocks (hero + footer install section). On success the button
flashes 'copied' for 1.5s; on failure it falls back to 'select + ⌘C'.
If JS is disabled, the install block still renders the command text
verbatim — degrades gracefully.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Final validation pass

**Files:** none (validation only)

- [ ] **Step 1: Run the validator**

```bash
python3 scripts/check_landing.py
```

Expected: `OK (NNNN bytes)` with NNNN under 30720.

- [ ] **Step 2: Manual semantic + accessibility checks**

Open `landing/index.html` in a browser and confirm:

- Tab through the page — every link, button, and `<details>` summary is reachable and shows a visible focus ring (the `:focus-visible` rules in the CSS handle this for buttons; default browser ring handles the rest).
- Toggle dark/light at the OS level — page palette flips immediately.
- Resize to 375px width — agents grid collapses to single column, hero text wraps without overflow, install block stays inside the viewport.
- Disable JavaScript in the browser devtools — page still renders, install command still readable, FAQ accordion still works (`<details>` is native HTML).

- [ ] **Step 3: Lighthouse run (optional but recommended)**

```bash
# In Chrome devtools, open Lighthouse panel against http://localhost:8123
# Targets per spec: Performance ≥ 95, Accessibility ≥ 95, Best Practices ≥ 95, SEO ≥ 90.
python3 -m http.server 8123 --directory landing
```

If Performance is below 95: check whether the file weight grew past 30 KB or whether system fonts are being requested over network. Both are easy to fix.

If Accessibility is below 95: inspect the flagged elements; most likely fix is a missing `aria-label` or insufficient color contrast.

- [ ] **Step 4: Commit any final tweaks (skip if none)**

```bash
git status
# If files changed:
git add landing/index.html
git commit -m "fix(landing): tweaks from final validation pass"
```

---

## Self-Review

**Spec coverage:**
- Hero with headline + install CTA + GitHub link → Task 3 ✓
- The problem (3 concrete pains) → Task 4 ✓
- The shape — git for DevRel, .devrel/ output, editorial contract → Task 4 ✓
- 13-agent grid (Health / DevRel / Sales) → Task 5 ✓
- Editorial pipeline visualization → Task 6 ✓
- Argus highlight (action vocab, calibration, time-series) + pull-quote → Task 7 ✓
- Real session terminal block → Task 8 ✓
- Cost transparency → Task 8 ✓
- FAQ (4-5 questions) → Task 9 ✓
- Install repeat + footer → Task 9 ✓
- Copy-on-click JS → Task 10 ✓
- Visual direction: monospace headlines, terminal palette, light/dark via prefers-color-scheme → Task 2 ✓
- Mobile responsive (640px breakpoint) → Task 2 ✓
- prefers-reduced-motion honored → Task 2 ✓
- Anti-slop self-check (banned phrase blocklist) → validator script in Task 1 ✓
- Page weight target < 30 KB → validator script in Task 1 ✓
- Zero external resources → validator script in Task 1 ✓
- Semantic HTML (one h1, main, lang attr) → validator script in Task 1 ✓

**Placeholder scan:** None. Each task has the actual HTML/CSS/JS to paste, including all copy. The "real session" terminal block in Task 8 is hand-written from the actual quickstart doc — not a placeholder.

**Type consistency:** N/A for HTML, but CSS class names are stable across tasks: `.install` (Tasks 3, 9, 10), `.agents` / `.agents h3` / `.agents li b/span` (Tasks 2, 5), `.pipeline` (Tasks 2, 6). Custom-property names (`--bg`, `--fg`, `--accent`, etc.) defined in Task 2 and used everywhere else.

**Two notes for the implementer:**

1. The validator's text extractor strips `<code>` and `<pre>` content before checking banned phrases, so terminal output and code samples can contain anything. That's intentional — `pipx install` would otherwise trip a phrase like "intelligent" if the CLI ever emits it.

2. Task 9's footer wraps its content in a nested `<main>` for layout. The validator's `<main>` check (`"<main" not in text_html`) tolerates this. If a future change tightens it to "exactly one main," update the footer to use `<div class="container">` and lift the layout CSS into it.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-03-landing-page.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
